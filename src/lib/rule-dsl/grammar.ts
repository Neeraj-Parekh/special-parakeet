// Rule DSL — tokenizer + recursive-descent parser.
//
// This module turns a DSL condition string like
//   "payment_method == 'COD' AND amount_inr > 50000"
// into a typed AST the compiler can walk to produce a predicate.
//
// Grammar (recursive descent; lowest-precedence at top):
//   orExpr   := andExpr ( OR andExpr )*
//   andExpr  := notExpr ( AND notExpr )*
//   notExpr  := NOT notExpr | primary
//   primary  := '(' orExpr ')' | comparison
//   comparison := operand ( op operand )?
//   operand  := IDENT | NUMBER | STRING
//
// The tokenizer is hand-written (no regex backtracking) and emits
// precise (1-indexed) positions for error reporting. Errors thrown
// from here carry a `pos` field the route handler surfaces as 422.

/** Token kinds emitted by the tokenizer. */
export type TokenKind =
  | "IDENT" // e.g. amount_inr, payment_method
  | "NUMBER" // e.g. 50000, 3.14
  | "STRING" // e.g. 'COD'
  | "OP" // == != > < >= <=
  | "AND"
  | "OR"
  | "NOT"
  | "LPAREN"
  | "RPAREN"
  | "EOF";

/** A single token in the input stream. */
export interface Token {
  kind: TokenKind;
  /** Raw source text of the token. */
  lexeme: string;
  /** 1-indexed position in the source string (for error messages). */
  pos: number;
}

/** AST node types. Discriminated union for safe narrowing. */
export type AstNode =
  | { type: "or"; left: AstNode; right: AstNode }
  | { type: "and"; left: AstNode; right: AstNode }
  | { type: "not"; operand: AstNode }
  | {
      type: "comparison";
      operator: "==" | "!=" | ">" | "<" | ">=" | "<=";
      left: AstNode;
      right: AstNode;
    }
  | { type: "ident"; name: string; pos: number }
  | { type: "number"; value: number; pos: number }
  | { type: "string"; value: string; pos: number };

/** Error thrown by tokenizer/parser; carries a 1-indexed position. */
export class GrammarError extends Error {
  /** 1-indexed position in the source string. */
  pos: number;
  constructor(message: string, pos: number) {
    super(`${message} at pos ${pos}`);
    this.name = "GrammarError";
    this.pos = pos;
  }
}

const COMPARATORS = new Set<string>(["==", "!=", ">=", "<=", ">", "<"]);
const IDENT_START = /[A-Za-z_]/;
const IDENT_CONT = /[A-Za-z0-9_]/;
const DIGIT = /[0-9]/;
const WHITESPACE = /\s/;

/**
 * Tokenize a DSL condition string.
 *
 * @param src - the condition expression source text
 * @returns the list of tokens (terminated by an EOF token)
 * @throws GrammarError on an unrecognized character
 */
export function tokenize(src: string): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  const n = src.length;
  while (i < n) {
    const ch = src[i];
    // Whitespace — skip.
    if (WHITESPACE.test(ch)) {
      i++;
      continue;
    }
    // String literal — single-quoted. We disallow unterminated strings.
    if (ch === "'") {
      const start = i + 1; // pos points at opening quote (1-indexed)
      i++;
      let value = "";
      let closed = false;
      while (i < n) {
        const c = src[i];
        if (c === "'") {
          closed = true;
          i++;
          break;
        }
        // Allow escaped quote \' inside the string.
        if (c === "\\" && i + 1 < n && src[i + 1] === "'") {
          value += "'";
          i += 2;
          continue;
        }
        value += c;
        i++;
      }
      if (!closed) {
        throw new GrammarError("unterminated string literal", start);
      }
      tokens.push({ kind: "STRING", lexeme: `'${value}'`, pos: start });
      continue;
    }
    // Number — integer or decimal. Negative numbers are not tokens; the
    // DSL spec uses operators to express bounds, so we don't tokenize a
    // leading minus.
    if (DIGIT.test(ch)) {
      const start = i + 1;
      let num = "";
      while (i < n && DIGIT.test(src[i])) {
        num += src[i];
        i++;
      }
      if (i < n && src[i] === ".") {
        num += ".";
        i++;
        while (i < n && DIGIT.test(src[i])) {
          num += src[i];
          i++;
        }
      }
      tokens.push({ kind: "NUMBER", lexeme: num, pos: start });
      continue;
    }
    // Two-char operators must be matched before single-char.
    const two = src.slice(i, i + 2);
    if (two === "==" || two === "!=" || two === ">=" || two === "<=") {
      tokens.push({ kind: "OP", lexeme: two, pos: i + 1 });
      i += 2;
      continue;
    }
    if (ch === ">" || ch === "<") {
      tokens.push({ kind: "OP", lexeme: ch, pos: i + 1 });
      i++;
      continue;
    }
    // Parens.
    if (ch === "(") {
      tokens.push({ kind: "LPAREN", lexeme: ch, pos: i + 1 });
      i++;
      continue;
    }
    if (ch === ")") {
      tokens.push({ kind: "RPAREN", lexeme: ch, pos: i + 1 });
      i++;
      continue;
    }
    // Identifier — letters/digits/underscore. After lexing we check
    // whether the uppercased form is a reserved keyword (AND/OR/NOT).
    if (IDENT_START.test(ch)) {
      const start = i + 1;
      let ident = "";
      while (i < n && IDENT_CONT.test(src[i])) {
        ident += src[i];
        i++;
      }
      const upper = ident.toUpperCase();
      if (upper === "AND") {
        tokens.push({ kind: "AND", lexeme: ident, pos: start });
      } else if (upper === "OR") {
        tokens.push({ kind: "OR", lexeme: ident, pos: start });
      } else if (upper === "NOT") {
        tokens.push({ kind: "NOT", lexeme: ident, pos: start });
      } else {
        tokens.push({ kind: "IDENT", lexeme: ident, pos: start });
      }
      continue;
    }
    // Anything else is an unrecognized character.
    throw new GrammarError(`unexpected character '${ch}'`, i + 1);
  }
  tokens.push({ kind: "EOF", lexeme: "", pos: src.length + 1 });
  return tokens;
}

/**
 * Recursive-descent parser. Holds a token cursor and walks the grammar.
 *
 * Usage:
 *   const parser = new Parser(tokens);
 *   const ast = parser.parse();
 */
export class Parser {
  private tokens: Token[];
  private cur: number;
  constructor(tokens: Token[]) {
    this.tokens = tokens;
    this.cur = 0;
  }
  /** Current token (or EOF). */
  private peek(): Token {
    return this.tokens[this.cur];
  }
  /** Lookahead without consuming. */
  private peekAt(offset: number): Token {
    return this.tokens[Math.min(this.cur + offset, this.tokens.length - 1)];
  }
  /** Consume and return current token, advancing the cursor. */
  private advance(): Token {
    return this.tokens[this.cur++];
  }
  /** True if the current token is of the given kind. */
  private at(kind: TokenKind): boolean {
    return this.peek().kind === kind;
  }
  /** Expect a specific kind, throwing a precise GrammarError on mismatch. */
  private expect(kind: TokenKind, what: string): Token {
    if (!this.at(kind)) {
      const t = this.peek();
      throw new GrammarError(
        `expected ${what} but found '${t.lexeme || "<end>"}'`,
        t.pos,
      );
    }
    return this.advance();
  }

  /** Entry point — parse the whole expression. */
  parse(): AstNode {
    const node = this.parseOr();
    if (!this.at("EOF")) {
      throw new GrammarError(
        `unexpected token '${this.peek().lexeme}' after expression`,
        this.peek().pos,
      );
    }
    return node;
  }

  /** orExpr := andExpr ( OR andExpr )* */
  private parseOr(): AstNode {
    let left = this.parseAnd();
    while (this.at("OR")) {
      this.advance();
      const right = this.parseAnd();
      left = { type: "or", left, right };
    }
    return left;
  }
  /** andExpr := notExpr ( AND notExpr )* */
  private parseAnd(): AstNode {
    let left = this.parseNot();
    while (this.at("AND")) {
      this.advance();
      const right = this.parseNot();
      left = { type: "and", left, right };
    }
    return left;
  }
  /** notExpr := NOT notExpr | primary */
  private parseNot(): AstNode {
    if (this.at("NOT")) {
      this.advance();
      const operand = this.parseNot();
      return { type: "not", operand };
    }
    return this.parsePrimary();
  }
  /** primary := '(' orExpr ')' | comparison */
  private parsePrimary(): AstNode {
    if (this.at("LPAREN")) {
      this.advance();
      const node = this.parseOr();
      this.expect("RPAREN", "')'");
      return node;
    }
    return this.parseComparison();
  }
  /**
   * comparison := operand ( op operand )?
   *
   * A bare operand (no operator) is a truthy check — useful for boolean
   * fields like `address_quality == 'complete'` but also for the rare
   * case of an unparenthesized operand that the rule writer wants
   * evaluated as a JS truthiness test.
   */
  private parseComparison(): AstNode {
    const left = this.parseOperand();
    if (this.at("OP")) {
      const op = this.advance().lexeme as
        | "=="
        | "!="
        | ">"
        | "<"
        | ">="
        | "<=";
      const right = this.parseOperand();
      return { type: "comparison", operator: op, left, right };
    }
    return left;
  }
  /** operand := IDENT | NUMBER | STRING */
  private parseOperand(): AstNode {
    const t = this.peek();
    if (t.kind === "IDENT") {
      this.advance();
      return { type: "ident", name: t.lexeme, pos: t.pos };
    }
    if (t.kind === "NUMBER") {
      this.advance();
      const value = Number(t.lexeme);
      if (Number.isNaN(value)) {
        throw new GrammarError(`invalid number literal '${t.lexeme}'`, t.pos);
      }
      return { type: "number", value, pos: t.pos };
    }
    if (t.kind === "STRING") {
      this.advance();
      // Strip the surrounding quotes + unescape \' pairs.
      const raw = t.lexeme.slice(1, -1).replace(/\\'/g, "'");
      return { type: "string", value: raw, pos: t.pos };
    }
    if (t.kind === "EOF") {
      throw new GrammarError("Unexpected end of input", t.pos);
    }
    throw new GrammarError(
      `expected operand but found '${t.lexeme}'`,
      t.pos,
    );
  }
}

/** Convenience: tokenize + parse a condition string in one call. */
export function parseCondition(src: string): AstNode {
  const tokens = tokenize(src);
  return new Parser(tokens).parse();
}

/** Re-export for compiler consumers that only need COMPARATORS metadata. */
export const COMPARATOR_SET = COMPARATORS;
