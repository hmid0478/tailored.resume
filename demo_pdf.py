#!/usr/bin/env python3
"""Demo script to generate a sample PDF with minimal_blue template."""

import os
import sys
import time

# Add the current directory to path to import from app.py
sys.path.insert(0, os.path.dirname(__file__))

# Import everything from app.py that we need
import app

def create_demo_resume():
    """Create a demo resume with sample data."""
    
    # Sample resume data with company locations
    resume_data = {
        "name": "John Doe",
        "title": "Senior Software Engineer",
        "contact": "San Francisco, CA | john.doe@email.com | (555) 123-4567 | linkedin.com/in/johndoe | github.com/johndoe",
        "sections": [
            {
                "type": "simple_list",
                "heading": "Summary",
                "items": [
                    "Senior Software Engineer with 8+ years of experience building scalable web applications and distributed systems. Expert in full-stack development with focus on React, Node.js, and cloud technologies. Proven track record of leading teams and delivering high-impact solutions at high-growth tech companies."
                ]
            },
            {
                "type": "skills",
                "heading": "Technical Skills",
                "items": [
                    {
                        "category": "Frontend Development",
                        "items": "React, TypeScript, Next.js, Redux, Tailwind CSS, HTML5, CSS3, JavaScript (ES6+)"
                    },
                    {
                        "category": "Backend Development",
                        "items": "Node.js, Python, Express, Django, REST APIs, GraphQL, Microservices"
                    },
                    {
                        "category": "Cloud & DevOps",
                        "items": "AWS, Docker, Kubernetes, CI/CD, Terraform, Lambda, EC2, S3"
                    },
                    {
                        "category": "Databases",
                        "items": "PostgreSQL, MongoDB, Redis, Elasticsearch, MySQL"
                    }
                ]
            },
            {
                "type": "experience",
                "heading": "Experience",
                "items": [
                    {
                        "company": "TechCorp Inc.",
                        "location": "San Francisco, CA",
                        "job_title": "Senior Software Engineer",
                        "dates": "01/2021 - Present",
                        "bullets": [
                            "Architected and led development of **microservices architecture** serving 2M+ daily users, improving system reliability by 40%",
                            "Mentored team of 5 engineers, implementing **code review best practices** and reducing production bugs by 35%",
                            "Implemented **CI/CD pipelines** using Jenkins and Docker, reducing deployment time from 2 hours to 15 minutes",
                            "Led migration from monolithic architecture to **microservices**, improving scalability and reducing infrastructure costs by 30%"
                        ]
                    },
                    {
                        "company": "InnovateTech Solutions",
                        "location": "Austin, TX",
                        "job_title": "Software Engineer",
                        "dates": "06/2018 - 12/2020",
                        "bullets": [
                            "Developed **React-based dashboard** for real-time analytics, used by 500+ enterprise clients",
                            "Built **RESTful APIs** using Node.js and Express, handling 100K+ requests per day",
                            "Implemented **automated testing suite** using Jest and Cypress, achieving 90% code coverage",
                            "Collaborated with product team to deliver features on **agile sprints**, reducing time-to-market by 25%"
                        ]
                    },
                    {
                        "company": "StartupXYZ",
                        "location": "New York, NY",
                        "job_title": "Junior Developer",
                        "dates": "01/2017 - 05/2018",
                        "bullets": [
                            "Developed **responsive web applications** using React and Redux for e-commerce platform",
                            "Integrated **third-party payment gateways** (Stripe, PayPal), processing $1M+ in transactions",
                            "Optimized **database queries** in PostgreSQL, reducing page load times by 50%",
                            "Participated in **code reviews** and agile development processes, improving code quality"
                        ]
                    }
                ]
            },
            {
                "type": "education",
                "heading": "Education",
                "items": [
                    {
                        "degree": "Bachelor of Science in Computer Science",
                        "school": "University of California, Berkeley",
                        "location": "Berkeley, CA",
                        "dates": "2013 - 2017"
                    }
                ]
            }
        ]
    }
    
    return resume_data

def generate_pdf(template_name="minimal_blue"):
    """Generate the demo PDF with specified template."""
    
    # Create output filename on Desktop
    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    output_file = os.path.join(desktop_path, f"demo_resume_{template_name}.pdf")
    
    # Delete existing file if it exists to ensure fresh generation
    if os.path.exists(output_file):
        try:
            os.remove(output_file)
            print(f"Deleted existing file: {output_file}")
        except Exception as e:
            print(f"Could not delete existing file: {e}")
    
    # Create ResumePDF instance with specified template
    pdf = app.ResumePDF(template=template_name)
    
    # Add first page
    pdf.add_page()
    
    # Set margins
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)
    pdf.set_top_margin(15)
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # Get resume data
    resume_data = create_demo_resume()
    
    # Render header (name + contact)
    pdf.set_font("Helvetica", "B" if pdf.cfg.get("name_bold", True) else "", pdf.cfg.get("name_size", 20))
    pdf.set_text_color(*pdf.DARK)
    name_align = pdf.cfg.get("name_align", "L")
    pdf.cell(0, 10, app._clean_text(resume_data.get("name", "")), align=name_align, new_x="LMARGIN", new_y="NEXT")
    
    # Contact info
    contact = app._clean_text(resume_data.get("contact", ""))
    if contact:
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*pdf.GRAY)
        contact_align = pdf.cfg.get("contact_align", "L")
        contact_parts = [p.strip() for p in contact.split("|") if p.strip()]
        if pdf.cfg.get("contact_stacked"):
            for part in contact_parts:
                pdf.multi_cell(0, 5, part, align=contact_align, new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.multi_cell(0, 5, " | ".join(contact_parts), align=contact_align, new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(4)
    
    # Render sections using the section renderers from app.py
    for section in resume_data.get("sections", []):
        sec_type = section.get("type", "simple_list")
        
        # Get the appropriate renderer function from app module
        if sec_type == "experience":
            app._render_section_experience(pdf, section)
        elif sec_type == "education":
            app._render_section_education(pdf, section)
        elif sec_type == "skills":
            app._render_section_skills(pdf, section)
        else:
            app._render_section_simple_list(pdf, section)
    
    # Save the PDF
    pdf.output(output_file)
    print(f"Demo PDF generated successfully: {output_file}")
    return output_file

if __name__ == "__main__":
    try:
        # List of all templates
        templates = ["minimal_clean", "modern_green", "classic_blue", "universal", "minimal_blue"]
        
        print(f"Generating {len(templates)} demo PDFs...")
        for template in templates:
            print(f"\nGenerating {template} demo...")
            result = generate_pdf(template)
            print(f"Generated: {result}")
        
        print("\nAll demo PDFs generated successfully!")
    except Exception as e:
        print(f"Error generating PDF: {e}")
        import traceback
        traceback.print_exc()
