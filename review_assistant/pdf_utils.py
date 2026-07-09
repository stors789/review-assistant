import markdown
import logging
from xhtml2pdf import pisa

logger = logging.getLogger(__name__)

def md_to_pdf(md_text: str, pdf_path: str):
    try:
        html = markdown.markdown(md_text, extensions=['tables', 'fenced_code'])
        # A template that supports Chinese characters via built-in system fonts on Mac/Win
        html_content = f"""
        <html>
        <head>
        <meta charset="utf-8">
        <style>
        @font-face {{
            font-family: 'system-fonts';
            src: local('PingFang SC'), local('Microsoft YaHei'), local('SimSun'), local('STHeiti');
        }}
        body {{
            font-family: 'system-fonts', sans-serif;
            line-height: 1.6;
            font-size: 12pt;
            padding: 20px;
        }}
        h1, h2, h3, h4 {{ color: #333; }}
        table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
        </style>
        </head>
        <body>
        {html}
        </body>
        </html>
        """
        with open(pdf_path, 'wb') as f:
            pisa_status = pisa.CreatePDF(html_content, dest=f)
        if pisa_status.err:
            logger.error("PDF generation failed.")
    except Exception as e:
        logger.error(f"Exception during PDF generation: {{e}}")

