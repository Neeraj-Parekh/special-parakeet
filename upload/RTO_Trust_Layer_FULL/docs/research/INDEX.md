# Research Bibliography — papers backing this project

> PDFs live locally in `paper_ref/` (git-ignored: redistribution rights vary by venue;
> this public repo carries the citations, not the paywalled files). Free/open-access
> copies already committed under `docs/research/`.

## A. Core ML methodology (model + experiments)
| Claim in repo | Citation | Local file |
|---|---|---|
| PR-AUC-primary for ~23% positives; resampling/ensemble context | H. He & E. Garcia, "Learning from Imbalanced Data," *IEEE TKDE* 21(9), 2009 | `paper_ref/haibohe2009.pdf` |
| Deep reference volume for the same | Fernández, García, Galar, Prati, Krawczyk, Herrera, *Learning from Imbalanced Data Sets*, Springer 2018 | `paper_ref/10.1007@978-3-319-98074-4.pdf` |
| Cost-optimal thresholds & threshold-curve analysis behind E4 | C. Drummond & R. Holte, "Cost Curves: An Improved Method for Visualizing Classifier Performance," *Machine Learning* 65, 2006. DOI 10.1007/s10994-006-8199-5 | `paper_ref/s10994-006-8199-5.pdf` |
| Delivery-success prediction from order + location features (feature-family ladder E1→E3) | "A prescriptive analytics framework for efficient E-commerce order delivery," *Decision Support Systems* 147, 113584, 2021 | `paper_ref/1-s2.0-S0167923621000944-main.pdf` |
| SHAP-based interpretability methodology for logistics-delay risk (reason codes) | Z. Hu et al., "ML-Based Prediction and Interpretability Analysis of Logistics Delay Risks in E-commerce Supply Chains," ACM 2025/26. DOI 10.1145/3779475.3779510 | `paper_ref/3779475.3779510.pdf` |
| PSI drift monitor + retrain-trigger design | J. Gama, I. Žliobaitė, A. Bifet, M. Pechenizkiy, A. Bouchachia, "A Survey on Concept Drift Adaptation," *ACM Computing Surveys* 46(4), 2014. DOI 10.1145/2523813 | `paper_ref/2523813.pdf` |

## B. Platform / MLOps credibility
| Claim | Citation | File |
|---|---|---|
| Production-scale ML platform pattern (registry, validation, serving) | Baylor et al., "TFX: A TensorFlow-Based Production-Scale Machine Learning Platform," *KDD'17* | `paper_ref/TFX_A_TensorFlow-Based_Production-Scale_Machine_Le.pdf` |
| Deployment-failure taxonomy motivating circuit breaker + monitoring | Paleyes, Urma, Lawrence, "Challenges in Deploying Machine Learning: a Survey of Case Studies," arXiv:2011.09926 (2022) | `paper_ref/2011.09926v3.pdf` |

## C. Trust layer / agentic commerce (the thesis)
| Claim | Citation | File |
|---|---|---|
| **Anchor**: payments-specific agent risks need regulation-grade guardrails | D. Restrepo Amariles, D. Charlotin, L. He-Guelton, "AI Agents in Payments: Applications, Risks and Regulations," *European Journal of Risk Regulation*, 2026. DOI 10.1017/err.2026.10103 | `paper_ref/ai-agents-in-payments-applications-risks-and-regulations.pdf` |
| Security SoK for agentic commerce (threat models our gates answer) | Mao, Wang, Liu, Zhu, Ma, Yan, "SoK: Security of Autonomous LLM Agents in Agentic Commerce," arXiv:2604.15367 (2026) | `paper_ref/2604.15367v2.pdf` |
| Cross-layer trustworthy-agent architecture review (maps to V2 layers) | "Trustworthy agentic AI systems: a cross-layer review of architectures, threat models, and governance strategies," *F1000Research* 14:905, 2025 | `paper_ref/de69d868-c126-4d6c-854a-8d7be1e8adef_f1000res169927.pdf` |
| Consumer-effects evidence for delegation in e-commerce | S. Balaskas, "From Recommendations to Delegation: A Systematic Review Mapping Agentic AI in E-Commerce and Its Consumer Effects," *Information* 17(2):222, MDPI, 2026 | `paper_ref/information-17-00222-with-cover.pdf` |
| Autonomy/accountability framing for agentic finance | A. Mukherjee, H. Chang, "Agentic AI: Autonomy, Accountability, and the Algorithmic Society," arXiv:2502.00289 (2025) | `paper_ref/2502.00289.pdf` |
| Liability architecture for autonomous financial agents | S. Faloore Ayomide, "Liability for Autonomous Financial Agents," SSRN 6402418, 2026 | `paper_ref/ssrn-6402418.pdf` |
| Governance/accountability of proxy agents | G. Lundholm, "Agentic Proxies: Governance, Accountability, and the Architecture of a Trustworthy AI Economy," SSRN 6952119, working draft 2026 | `paper_ref/ssrn-6952119.pdf` |
| Right-to-explanation basis for mandatory reason codes | B. Goodman, S. Flaxman, "EU Regulations on Algorithmic Decision-Making and a 'Right to Explanation'," *AI Magazine* 38(3), 2017 | `paper_ref/goodman2017.pdf` |
| Human-AI trust in finance (bibliometric grounding for REVIEW gates) | M. Mirabile, G.E. Corradi, "Trust in human–AI collaboration in finance," *AI & Society*, 2026. DOI 10.1007/s00146-026-03049-y | `paper_ref/s00146-026-03049-y.pdf` |
| Delegation-to-agents societal review | M. Hasselwander et al., "AI agent, take over?! Task delegation to agentic AI systems," *AI & Society*, 2026. DOI 10.1007/s00146-026-03060-3 | `paper_ref/s00146-026-03060-3.pdf` |
| Governance when agents act autonomously | "When AI Agents Act: Governance, Accountability, and Strategic Risk in Autonomous Systems," *IJRSI* XII(XII), 2025 | `paper_ref/vol12-iss12-pg547-612-202601_pdf.pdf` |
| Agentic-AI-as-a-service innovation context | *Journal of Innovation & Knowledge* 14, 101039, Elsevier 2026 | `paper_ref/main.pdf` |

## D. Held in reserve / not cited
- `annas-arch-96f7578c490a.pdf` (IEEE ICAIC 2026 proc.) — provenance unclear; excluded from public citations.
- Off-topic: green-supply-chain, eyewear PIA, open-fabric, HotCloudPerf cloud-costs, digital product passports, Global Prosperity items, oosthuizen2020 (retail marketing).
- Still missing: Bahnsen et al. ICMLA 2013 (Bayes-minimum-risk cost-sensitive fraud detection); NPCI OC-201-B circular (browser-gated).

## Free copies committed in-repo (`docs/research/`)
Tramèr et al. USENIX'16 (model extraction → rate-limit controls) · NIST AI RMF 1.0 · FRAUD-RLA arXiv'25.
