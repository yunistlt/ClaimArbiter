import os
import requests
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
import logging

logger = logging.getLogger(__name__)

# URL for a font that supports Cyrillic (Roboto)
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/roboto/Roboto-Regular.ttf"
FONT_PATH = "assets/fonts/Roboto-Regular.ttf"
FONT_NAME = "Roboto"

def ensure_font():
    """Ensure the font file exists, download if necessary."""
    if not os.path.exists("assets/fonts"):
        os.makedirs("assets/fonts", exist_ok=True)
        
    if not os.path.exists(FONT_PATH):
        logger.info(f"Downloading font from {FONT_URL}...")
        try:
            response = requests.get(FONT_URL, timeout=10) # Add timeout to prevent hanging
            response.raise_for_status()
            with open(FONT_PATH, "wb") as f:
                f.write(response.content)
            logger.info("Font downloaded successfully.")
        except Exception as e:
            logger.error(f"Failed to download font: {e}")

def create_pdf(text: str, output_path: str) -> bool:
    """
    Generates a PDF file with the given text content using ReportLab and a Cyrillic-supporting font.
    """
    ensure_font()
    
    # Register Font
    if os.path.exists(FONT_PATH):
        try:
            pdfmetrics.registerFont(TTFont(FONT_NAME, FONT_PATH))
            font_to_use = FONT_NAME
        except Exception as e:
            logger.error(f"Could not register font {FONT_PATH}: {e}")
            font_to_use = "Helvetica" # Fallback, but won't support Cyrillic well
    else:
        logger.warning("Font not found, using default Helvetica (no Cyrillic support)")
        font_to_use = "Helvetica"

    try:
        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            rightMargin=50, leftMargin=50,
            topMargin=50, bottomMargin=50
        )

        styles = getSampleStyleSheet()
        # Create a custom style for our document body
        body_style = ParagraphStyle(
            name='CyrillicBody',
            parent=styles['Normal'],
            fontName=font_to_use,
            fontSize=11,
            leading=14,
            alignment=TA_LEFT,
            spaceAfter=10
        )
        
        story = []
        
        # Split text into paragraphs
        # We handle double newlines as paragraph breaks
        text = text.replace('\r\n', '\n')
        paragraphs = text.split('\n')
        
        for p_text in paragraphs:
            stripped = p_text.strip()
            if not stripped:
                story.append(Spacer(1, 6))
                continue
                
            # Basic XML escaping for ReportLab Paragraph
            # ReportLab supports some tags like <b>, <i>, but LLM output might contain raw chars
            safe_text = stripped.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            
            # Re-enable basic formatting if we trust LLM to not check formatting, 
            # OR just strip it. For now, treat as plain text mainly.
            # Convert **text** to <b>text</b> for bold (common markdown)
            while "**" in safe_text:
                safe_text = safe_text.replace("**", "<b>", 1)
                safe_text = safe_text.replace("**", "</b>", 1)
                
            story.append(Paragraph(safe_text, body_style))

        doc.build(story)
        logger.info(f"PDF generated at {output_path}")
        return True
    except Exception as e:
        logger.error(f"PDF Build failed: {e}")
        return False
