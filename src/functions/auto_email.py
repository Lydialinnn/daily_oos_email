import smtplib
import ssl
import html
import os # Added for filename extraction
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase # Added for attachments
from email import encoders # Added for encoding attachments

import pandas as pd # don't use as pd. will be imported as em.pd

class HTMLConverter:
    """
    A class to handle conversions to HTML format.  This includes:
      - Pandas DataFrames
      - Dictionaries
      - Lists
      - Strings (including those that might contain ANSI escape codes)
    """

    def df_to_html(self, df: pd.DataFrame) -> str:
        """Converts a Pandas DataFrame to an HTML table."""
        if df.empty:
            return "<p>No data available.</p>"
        return df.to_html(index=False, escape=False)

    def dict_to_html(self, data: dict) -> str:
        """Converts a dictionary to an HTML table."""
        if not data:
            return "<p>No data available.</p>"  # Handle empty dictionary
        html = "<table>"
        for key, value in data.items():
            # Escape special HTML characters in key and value
            html += f"<tr><td>{html.escape(str(key))}</td><td>{html.escape(str(value))}</td></tr>"
        html += "</table>"
        return html

    def list_to_html(self, data: list) -> str:
        """Converts a list to an HTML unordered list."""
        if not data:
            return "<p>No data available.</p>"
        html = "<ul>"
        for item in data:
            # Escape special HTML characters in the item
            html += f"<li>{html.escape(str(item))}</li>"
        html += "</ul>"
        return html


    def print_to_html(self, captured_output: str) -> str:
        """
        Converts captured print output (which may contain ANSI escape codes) to HTML.
        The output is wrapped in <pre> tags to preserve formatting and
        potentially render ANSI colors if the browser supports it.

        Args:
            captured_output: The string captured from print statements.

        Returns:
            An HTML string.
        """
        html_output = html.escape(captured_output).replace("\n", "<br>")
        return f"<pre style='font-family: monospace; font-size: 14px;'>{html_output}</pre>"  # Add style to pre tag
    
def send_email(sender_email, sender_password, receiver_emails, subject, body, attachments=[]):
    """
    Sends an email to one or more recipients.

    Args:
        sender_email: The sender's email address.
        sender_password: The sender's email password.
        receiver_emails:  A string (for a single recipient) or a list of strings
                         (for multiple recipients).
        subject: The email subject.
        body: The email body (HTML content).
        attachments: Optional; A list of file paths for the attachments. Defaults to None.
    """
    port = 465  # For SSL
    smtp_server = "smtp.gmail.com"

    message = MIMEMultipart()
    message["From"] = sender_email
    message["Subject"] = subject

    # Handle single recipient (string) or multiple recipients (list)
    if isinstance(receiver_emails, str):
        message["To"] = receiver_emails  # Single recipient
    elif isinstance(receiver_emails, list):
        message["To"] = ", ".join(receiver_emails)  # Comma-separated string for multiple
    else:
        raise TypeError("receiver_emails must be a string or a list of strings")

    message.attach(MIMEText(body, "html"))

    # --- Add Attachment Handling ---
    if attachments:
        if not isinstance(attachments, list):
            attachments = [attachments] # Make it a list if only one path string is passed

        for file_path in attachments:
            try:
                # Open file in binary mode
                with open(file_path, "rb") as attachment_file:
                    # Create a MIMEBase object
                    part = MIMEBase("application", "octet-stream") # Generic binary type
                    part.set_payload(attachment_file.read())

                # Encode file in ASCII characters to send by email
                encoders.encode_base64(part)

                # Add header as key/value pair to identify the attachment
                filename = os.path.basename(file_path)
                part.add_header(
                    "Content-Disposition",
                    f"attachment; filename= {filename}",
                )

                # Add attachment to message
                message.attach(part)
            except FileNotFoundError:
                print(f"Warning: Attachment file not found: {file_path}. Skipping.")
            except Exception as e:
                print(f"Warning: Could not attach file {file_path}. Error: {e}. Skipping.")
    # --- End Attachment Handling ---

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(smtp_server, port, context=context) as server:
            server.login(sender_email, sender_password)
            # Use the recipient list directly in sendmail.  The SMTP server
            # handles multiple recipients correctly when given a list.
            server.sendmail(sender_email, receiver_emails, message.as_string())
        print("Email sent successfully!","\n")
    except Exception as e:
        print(f"Error sending email: {e}")




import os
import mimetypes
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage # <-- Import this for inline images
from email import encoders
import smtplib # For sending email

def send_email_pgn(sender_email, sender_password, recipients, subject, body_content, inline_image_paths=None, inline_image_cids=None):
    # The main message should be 'related' for HTML body with inline images
    message = MIMEMultipart("related")
    message["From"] = sender_email
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject

    # Attach the HTML body (this must be a string)
    message.attach(MIMEText(body_content, "html"))

    # Embed inline images
    if inline_image_paths and inline_image_cids: # Check if paths and CIDs are provided
        for file_path in inline_image_paths:
            if os.path.exists(file_path):
                cid = inline_image_cids.get(file_path) # Get the CID for this specific image
                if cid:
                    with open(file_path, "rb") as img_file:
                        img = MIMEImage(img_file.read())
                        img.add_header("Content-ID", f"<{cid}>") # CID MUST be enclosed in angle brackets
                        img.add_header("Content-Disposition", "inline", filename=os.path.basename(file_path))
                        message.attach(img)
                else:
                    print(f"Warning: No Content-ID found for {file_path}. It won't be embedded.")
            else:
                print(f"Warning: Image file not found: {file_path}. Skipping embedding.")

    # If you also need to attach OTHER files (not inline images),
    # you'd need a separate argument for them and use MIMEBase as in previous examples.
    # For now, we're assuming attachment_file_paths are only for inline images.


    # --- Email Sending Logic ---
    try:
        # Example for Gmail SMTP, adjust for your email provider
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.send_message(message)
        print("Email sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")