from pypdf import PdfReader, PdfWriter

A4_W, A4_H = 595.28, 841.89

def normalize(page):
    w, h = float(page.mediabox.width), float(page.mediabox.height)
    if abs(w - A4_W) > 0.4 or abs(h - A4_H) > 0.4:
        page.scale_to(A4_W, A4_H)
    return page

writer = PdfWriter()
cover = PdfReader("/home/z/my-project/analysis/report-pdf/cover.pdf").pages[0]
writer.add_page(normalize(cover))
for p in PdfReader("/home/z/my-project/analysis/report-pdf/report_body.pdf").pages:
    writer.add_page(normalize(p))
writer.add_metadata({
    "/Title": "RTO Trust Layer - Project Report",
    "/Author": "Neeraj Ganesh Parekh",
    "/Creator": "RTO Trust Layer — report build pipeline",
    "/Subject": "Fraud-risk decision engine for Indian COD e-commerce",
})
out = "/home/z/my-project/PROJECT_REPORT.pdf"
with open(out, "wb") as f:
    writer.write(f)
print("merged ->", out, "pages:", len(writer.pages))
