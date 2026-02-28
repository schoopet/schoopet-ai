#!/usr/bin/env python3
"""One-time offline helper: generate a synthetic Jane Smith resume PDF.

Usage:
    pip install fpdf2
    python make_resume.py

Prints the base64-encoded PDF to stdout. Paste the output into the
`data` field of the inline_data parts in email_workflow.evalset.json.

This script is NOT imported by any tests - fpdf2 is not required in
requirements.txt.
"""
import base64
import sys

try:
    from fpdf import FPDF
except ImportError:
    print("ERROR: fpdf2 not installed. Run: pip install fpdf2", file=sys.stderr)
    sys.exit(1)


def make_resume_pdf() -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_margins(20, 20, 20)

    # Name / contact header
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 10, "Jane Smith", new_x="LMARGIN", new_y="NEXT", align="C")

    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, "jane.smith@gmail.com  |  +1-555-0100", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.cell(
        0, 6, "linkedin.com/in/janesmith  |  github.com/janesmith",
        new_x="LMARGIN", new_y="NEXT", align="C"
    )
    pdf.ln(4)

    def section(title: str):
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_fill_color(230, 230, 230)
        pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT", fill=True)
        pdf.set_font("Helvetica", "", 11)
        pdf.ln(1)

    def bullet(text: str):
        pdf.cell(6, 6, "-", new_x="RIGHT", new_y="TOP")
        pdf.multi_cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")

    # Summary
    section("Summary")
    pdf.multi_cell(
        0, 6,
        "Software engineer with 6 years of experience building scalable distributed systems. "
        "Passionate about data infrastructure, cloud-native architectures, and developer tooling.",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(2)

    # Experience
    section("Experience")
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Senior Software Engineer - TechCorp", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(0, 5, "January 2020 - February 2026  |  San Francisco, CA", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    bullet("Led migration of monolithic data pipeline to microservices on Kubernetes, reducing latency by 40%.")
    bullet("Designed BigQuery-based analytics platform serving 50M+ events/day.")
    bullet("Mentored a team of 4 junior engineers; drove adoption of Go for new services.")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "Software Engineer - StartupXYZ", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(0, 5, "June 2017 - December 2019  |  Remote", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    bullet("Built REST and gRPC APIs in Python and Go for a SaaS analytics product.")
    bullet("Implemented PostgreSQL query optimizations cutting report generation time by 60%.")
    pdf.ln(2)

    # Education
    section("Education")
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, "B.S. Computer Science - UC Berkeley", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, "Class of 2019  |  GPA: 3.8 / 4.0", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Skills
    section("Skills")
    bullet("Languages: Python, Go, SQL, TypeScript")
    bullet("Infrastructure: Kubernetes, Docker, Terraform, GCP (BigQuery, Pub/Sub, Cloud Run)")
    bullet("Databases: PostgreSQL, Redis, Firestore")
    bullet("Other: gRPC, REST API design, CI/CD (GitHub Actions, Cloud Build)")

    return bytes(pdf.output())


if __name__ == "__main__":
    pdf_bytes = make_resume_pdf()
    encoded = base64.b64encode(pdf_bytes).decode("ascii")
    print(encoded)
