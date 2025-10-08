import imaplib
import email
from email.header import decode_header
import logging
from datetime import datetime, timedelta

from nf_processor import Config, DatabaseManager, XMLExtractor, ValidadorNF, PDFExtractor

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Lista de palavras-chave para filtrar no assunto (em minúsculo)
PALAVRAS_CHAVE = ["danfe", "nf-e", "nfc-e", "nf", "nfe", "xml", "nota fiscal"]

def buscar_e_processar_emails():
    try:
        config = Config()
        db_manager = DatabaseManager(config)
    except Exception as e:
        logger.error(f"ROBÔ: Erro ao carregar configuração ou conectar ao banco: {e}")
        return

    logger.info("ROBÔ: Iniciando processo de busca de notas fiscais no e-mail...")
    try:
        mail = imaplib.IMAP4_SSL(config.IMAP_SERVER, config.IMAP_PORT)
        mail.login(config.EMAIL_USER, config.EMAIL_PASSWORD)
        mail.select('inbox')
        
        # Busca e-mails não lidos
        status, messages = mail.search(None, 'UNSEEN')
        
        if status != 'OK' or not messages[0]:
            logger.info("ROBÔ: Nenhuma mensagem nova encontrada.")
            mail.logout()
            return
            
        email_ids = messages[0].split()
        logger.info(f"ROBÔ: Encontrados {len(email_ids)} e-mails novos.")

        for email_id in email_ids:
            status, msg_data = mail.fetch(email_id, '(RFC822)')
            if status != 'OK': continue

            msg = email.message_from_bytes(msg_data[0][1])

            # Decodifica o assunto
            subject_parts = decode_header(msg["Subject"])
            subject_decoded = []

            for part, enc in subject_parts:
                if isinstance(part, bytes):
                    # Se o encoding for None, 'unknown-8bit' ou inválido, usa fallback
                    if not enc or enc.lower() == "unknown-8bit":
                        enc = "utf-8"
                    try:
                        subject_decoded.append(part.decode(enc, errors="ignore"))
                    except LookupError:  # caso encoding não seja reconhecido
                        subject_decoded.append(part.decode("utf-8", errors="ignore"))
                else:
                    subject_decoded.append(part)

            # Junta tudo em uma string
            subject = " ".join(subject_decoded).strip()
            subject_lower = subject.lower() if subject else ""

            # Verifica se contém alguma palavra-chave
            if not any(palavra in subject_lower for palavra in PALAVRAS_CHAVE):
                logger.info(f"ROBÔ: E-mail ignorado (assunto sem palavras-chave): {subject}")
                # Marca como lido para não processar de novo
                mail.store(email_id, '+FLAGS', '\\Seen')
                continue

            logger.info(f"ROBÔ: Processando e-mail com assunto: {subject}")

            for part in msg.walk():
                if part.get_content_maintype() == 'multipart' or part.get('Content-Disposition') is None:
                    continue
                
                filename = part.get_filename()
                if not filename:
                    continue

                filename_lower = filename.lower()

                # 📄 XML
                if filename_lower.endswith('.xml'):
                    logger.info(f"ROBÔ: Anexo XML encontrado: {filename}")
                    xml_content = part.get_payload(decode=True)
                    nota = XMLExtractor.extrair_dados_xml(xml_content)
                    
                    if nota and ValidadorNF.validar_nota_fiscal(nota):
                        db_manager.salvar_nota_fiscal(nota)
                    elif nota:
                        logger.warning(f"ROBÔ: NF {nota.numero or 'S/N'} inválida.")
                    else:
                        logger.error(f"ROBÔ: Falha ao extrair dados do XML '{filename}'.")

                # 📄 PDF
                elif filename.endswith('.pdf'):
                    logger.info(f"ROBÔ: Anexo PDF encontrado: {filename}")
                    pdf_content = part.get_payload(decode=True)
                    
                    nota = PDFExtractor.extrair_dados_pdf(pdf_content)
                    if nota and ValidadorNF.validar_nota_fiscal(nota):
                        db_manager.salvar_nota_fiscal(nota)
                    elif nota:
                        logger.warning(f"ROBÔ: NF {nota.numero or 'S/N'} inválida.")
                    else:
                        logger.error(f"ROBÔ: Falha ao extrair dados do PDF '{filename}'.")

            # Marca como lido
            mail.store(email_id, '+FLAGS', '\\Seen')

        mail.logout()
    except imaplib.IMAP4.error as e:
        logger.error(f"ROBÔ: Erro de IMAP: {e}")
    except Exception as e:
        logger.error(f"ROBÔ: Ocorreu um erro inesperado: {e}")

if __name__ == "__main__":
    buscar_e_processar_emails()
