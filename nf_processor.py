# Nome do arquivo: nf_processor.py (Versão Final Corrigida)

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from sqlalchemy import create_engine, text, exc
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import plotly.express as px
from pathlib import Path
import tempfile
from dotenv import load_dotenv
import google.generativeai as genai
from decimal import Decimal, InvalidOperation
import xml.etree.ElementTree as ET
import re

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- CLASSES DE LÓGICA DE NEGÓCIO ---

import io
import pdfplumber

class PDFExtractor:
    @staticmethod
    def extrair_dados_pdf(pdf_bytes):
        try:
            def extrair_valor(pattern, texto, default=None):
                match = re.search(pattern, texto, re.IGNORECASE)
                if (match != None): 
                    if (match.group(1) != None): 
                        return match.group(1)
                    else: 
                        return default
                else: 
                    return default
            # Lê o PDF diretamente da memória
            pdf_stream = io.BytesIO(pdf_bytes)
            
            texto_completo = ""
            with pdfplumber.open(pdf_stream) as pdf:
                texto_completo = "\n".join(page.extract_text() or "" for page in pdf.pages)

            # Remove espaços duplos e normaliza
            texto_completo = re.sub(r"\s+", " ", texto_completo)

            # --- Expressões Regulares para DANFE ---
            cnpj_emitente = extrair_valor(r"CNPJ\s*[:\s]*([\d\.\-/]{14,18})", texto_completo,"CNPJ 00.111.111/0001-11")
            numero_nf = extrair_valor(r"N[º°]\s*(\d{1,9})", texto_completo, "Nº: 0")
            data_emissao = extrair_valor(r"(?:Data\s+(?:de\s+)?Emiss[aã]o|Emiss[aã]o)\s*:?[\s]*(\d{2}/\d{2}/\d{4})", texto_completo, datetime.today().date())
            serie=extrair_valor(r"S[ée]rie\s*:?[\s]*([0-9]{1,3})", texto_completo,"SÉRIE 0")
            nome_emitente=extrair_valor(r"(?:Emitente\s*:?[\s]*(.+)|recebemos\s+de\s+(.+?)\s+os\s+produtos)", texto_completo, "EMITENTE NÃO ENCONTRADO")
            valor_total=extrair_valor(r"Valor\s+Total\s+(?:da\s+(?:Nota|nf-?e)|Nota)\s*(?:R\$)?\s*([\d\.,]+)", texto_completo, 0)
            chave_acesso=extrair_valor(r"((?:\d{4}\s*){11})", texto_completo, "0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000")
            natureza_operacao=extrair_valor(r"^natureza\s+(?:da|de\s+)?opera[cç][aã]o$", texto_completo, "SEM NATUREZA")

            # Cria o objeto NotaFiscal

            nota = NotaFiscal(
                numero=numero_nf,
                cnpj_emitente=cnpj_emitente,
                data_emissao=data_emissao,
                serie=serie,
                nome_emitente=nome_emitente,
                valor_total=valor_total,
                chave_acesso=chave_acesso,
                natureza_operacao=natureza_operacao
            )

            return nota

        except Exception as e:
            print(f"ROBÔ: Erro ao processar PDF: {e}")
            return None


class XMLExtractor:
    @staticmethod
    def _safe_float(value, default=0.0):
        try:
            return float(value) if value else default
        except (ValueError, TypeError):
            return default

    @staticmethod
    def extrair_dados_xml(xml_content: bytes) -> Optional['NotaFiscal']:
        try:
            xml_string = xml_content.decode('utf-8', errors='ignore')
            root = ET.fromstring(xml_string)
            ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
            inf_nfe = root.find('.//nfe:infNFe', ns)
            if inf_nfe is None:
                inf_nfe = root.find('.//infNFe'); ns = {}
            if inf_nfe is None: return None
            ide = inf_nfe.find('.//ide' if not ns else './/nfe:ide', ns)
            emit = inf_nfe.find('.//emit' if not ns else './/nfe:emit', ns)
            dest = inf_nfe.find('.//dest' if not ns else './/nfe:dest', ns)
            total = inf_nfe.find('.//total' if not ns else './/nfe:total', ns)
            icms_tot = total.find('.//ICMSTot' if not ns else './/nfe:ICMSTot', ns)
            return NotaFiscal(
                numero=XMLExtractor._get_text(ide, 'nNF', ns),
                serie=XMLExtractor._get_text(ide, 'serie', ns),
                data_emissao=XMLExtractor._parse_date(XMLExtractor._get_text(ide, 'dhEmi', ns)),
                cnpj_emitente=XMLExtractor._get_text(emit, 'CNPJ', ns),
                nome_emitente=XMLExtractor._get_text(emit, 'xNome', ns),
                cnpj_destinatario=XMLExtractor._get_text(dest, 'CNPJ', ns),
                nome_destinatario=XMLExtractor._get_text(dest, 'xNome', ns),
                valor_total=XMLExtractor._safe_float(XMLExtractor._get_text(icms_tot, 'vNF', ns)),
                valor_icms=XMLExtractor._safe_float(XMLExtractor._get_text(icms_tot, 'vICMS', ns)),
                valor_ipi=XMLExtractor._safe_float(XMLExtractor._get_text(icms_tot, 'vIPI', ns)),
                valor_pis=XMLExtractor._safe_float(XMLExtractor._get_text(icms_tot, 'vPIS', ns)),
                valor_cofins=XMLExtractor._safe_float(XMLExtractor._get_text(icms_tot, 'vCOFINS', ns)),
                chave_acesso=inf_nfe.get('Id', '').replace('NFe', ''),
                natureza_operacao=XMLExtractor._get_text(ide, 'natOp', ns),
                itens=XMLExtractor._extrair_itens(inf_nfe, ns),
                xml_original=xml_string
            )
        except Exception as e:
            logger.error(f"Erro CRÍTICO ao extrair XML: {e}")
            return None
    @staticmethod
    def _get_text(element, tag, ns):
        if element is None: return ""
        node = element.find(f".//nfe:{tag}", ns) if ns else element.find(f".//{tag}")
        return node.text if node is not None else ""
    @staticmethod
    def _parse_date(date_str):
        if not date_str: return None
        try:
            return datetime.fromisoformat(date_str.split('T')[0])
        except:
            return None
    @staticmethod
    def _extrair_itens(inf_nfe, ns):
        itens = []
        for det in inf_nfe.findall('.//nfe:det', ns) if ns else inf_nfe.findall('.//det'):
            prod = det.find('.//nfe:prod', ns) if ns else det.find('.//prod')
            if prod:
                itens.append({
                    'codigo': XMLExtractor._get_text(prod, 'cProd', ns),
                    'descricao': XMLExtractor._get_text(prod, 'xProd', ns),
                    'ncm': XMLExtractor._get_text(prod, 'NCM', ns),
                    'quantidade': XMLExtractor._safe_float(XMLExtractor._get_text(prod, 'qCom', ns)),
                    'valor_unitario': XMLExtractor._safe_float(XMLExtractor._get_text(prod, 'vUnCom', ns)),
                    'valor_total': XMLExtractor._safe_float(XMLExtractor._get_text(prod, 'vProd', ns))
                })
        return itens

class ValidadorNF:
    @staticmethod
    def validar_nota_fiscal(nota: 'NotaFiscal') -> bool:
        if (isinstance(nota.valor_total, str)): nota.valor_total = float(nota.valor_total.replace(",", "."))
        if not all([nota.numero, nota.serie, nota.cnpj_emitente, nota.chave_acesso, nota.valor_total > 0, nota.data_emissao]):
            logger.warning(f"Validação falhou: campos obrigatórios ausentes na NF {nota.numero or 'S/N'}.")
            return False
        return True

# --- CLASSES DE DADOS E CONFIGURAÇÃO ---

@dataclass
class Config:
    IMAP_SERVER: str = os.getenv("IMAP_SERVER", "imap.gmail.com")
    IMAP_PORT: int = int(os.getenv("IMAP_PORT", 993))
    EMAIL_USER: str = os.getenv("EMAIL_USER", "")
    EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

@dataclass
class NotaFiscal:
    numero: str
    serie: str
    data_emissao: datetime
    cnpj_emitente: str
    nome_emitente: str
    valor_total: float
    chave_acesso: str
    natureza_operacao: str
    situacao: str = 'Pendente'
    data_vencimento: Optional[datetime] = None
    cnpj_destinatario: Optional[str] = None
    nome_destinatario: Optional[str] = None
    valor_icms: float = 0.0
    valor_ipi: float = 0.0
    valor_pis: float = 0.0
    valor_cofins: float = 0.0
    itens: List[Dict[str, Any]] = None
    xml_original: Optional[str] = None
    processado_em: datetime = None

    def __post_init__(self):
        if self.processado_em is None:
            self.processado_em = datetime.now()
        if self.itens is None:
            self.itens = []

# --- MÓDULO DE BANCO DE DADOS ---

class DatabaseManager:
    def __init__(self, config: Config):
        if not config.DATABASE_URL:
            raise ValueError("A URL do banco de dados (DATABASE_URL) não foi configurada.")
        try:
            connect_args = {'connect_timeout': 10}
            self.engine = create_engine(
                config.DATABASE_URL,
                connect_args=connect_args,
                json_serializer=json.dumps
            )
            with self.engine.connect() as connection:
                logger.info("Conexão com o banco de dados PostgreSQL estabelecida com sucesso.")
        except Exception as e:
            logger.error(f"Falha CRÍTICA ao conectar ao banco de dados: {e}")
            raise

    def log_processamento(self, tipo: str, arquivo: str, status: str, mensagem: str = ""):
        query = text("INSERT INTO logs_processamento (tipo_operacao, arquivo_processado, status, mensagem_erro, timestamp) VALUES (:tipo, :arquivo, :status, :msg, NOW())")
        try:
            with self.engine.connect() as connection:
                trans = connection.begin()
                connection.execute(query, {"tipo": tipo, "arquivo": arquivo, "status": status, "msg": mensagem})
                trans.commit()
        except Exception as e:
            logger.error(f"Falha GRAVE ao registrar log no banco de dados: {e}")

    def salvar_nota_fiscal(self, nota: NotaFiscal) -> bool:
        check_query = text("SELECT id FROM notas_fiscais WHERE chave_acesso = :chave")
        try:
            with self.engine.connect() as connection:
                if connection.execute(check_query, {"chave": nota.chave_acesso}).fetchone():
                    logger.warning(f"Nota fiscal {nota.numero} (Chave: {nota.chave_acesso}) já existe. Pulando.")
                    return False
        except Exception as e:
             logger.error(f"Erro ao verificar duplicidade da NF {nota.numero}: {e}")
             return False

        nf_data = asdict(nota)
        itens = nf_data.pop('itens')
        
        for key, value in nf_data.items():
            if isinstance(value, float):
                nf_data[key] = Decimal(str(value))

        insert_nf_query = text("INSERT INTO notas_fiscais (numero, serie, data_emissao, cnpj_emitente, nome_emitente, valor_total, chave_acesso, natureza_operacao, situacao, data_vencimento, cnpj_destinatario, nome_destinatario, valor_icms, valor_ipi, valor_pis, valor_cofins, xml_original, processado_em) VALUES (:numero, :serie, :data_emissao, :cnpj_emitente, :nome_emitente, :valor_total, :chave_acesso, :natureza_operacao, :situacao, :data_vencimento, :cnpj_destinatario, :nome_destinatario, :valor_icms, :valor_ipi, :valor_pis, :valor_cofins, :xml_original, :processado_em) RETURNING id")
        insert_item_query = text("INSERT INTO itens_nota_fiscal (nota_fiscal_id, codigo, descricao, ncm, quantidade, valor_unitario, valor_total) VALUES (:nota_id, :codigo, :descricao, :ncm, :quantidade, :valor_unitario, :valor_total)")

        try:
            with self.engine.begin() as connection:
                result = connection.execute(insert_nf_query, nf_data)
                nf_id = result.fetchone()[0]
                if itens and nf_id:
                    for item in itens:
                        for key, value in item.items():
                            if isinstance(value, float): item[key] = Decimal(str(value))
                        item['nota_id'] = nf_id
                        connection.execute(insert_item_query, item)
            logger.info(f"SUCESSO! Nota fiscal {nota.numero} salva no banco de dados.")
            return True
        except (exc.SQLAlchemyError, InvalidOperation, TypeError) as e:
            logger.error(f"ERRO AO SALVAR NF {nota.numero}: {e}\nDADOS: {nf_data}")
            self.log_processamento("Salvar NF", f"NF {nota.numero}", "Erro de Gravação", str(e))
            return False

    def buscar_dados(self, table_name: str, filtros: Dict = None) -> List[Dict]:
        base_query = f"SELECT * FROM {table_name}"
        where_clauses, params = [], {}
        if filtros:
            for col, val in filtros.items():
                if col.endswith('_inicio'): where_clauses.append(f"{col.replace('_inicio', '')} >= :{col}"); params[col] = val
                elif col.endswith('_fim'): where_clauses.append(f"{col.replace('_fim', '')} <= :{col}"); params[col] = val
                else: where_clauses.append(f"{col} = :{col}"); params[col] = val
        if where_clauses: base_query += " WHERE " + " AND ".join(where_clauses)
        base_query += " ORDER BY id DESC"
        try:
            with self.engine.connect() as connection:
                # LINHA CORRIGIDA ABAIXO
                return connection.execute(text(base_query), params).mappings().all()
        except Exception as e:
            logger.error(f"Erro ao buscar dados da tabela {table_name}: {e}")
            st.error(f"Erro de Banco de Dados: Não foi possível buscar os dados da tabela '{table_name}'.")
            return []

# --- MÓDULOS DE IA E DASHBOARD ---

class GeminiChat:
    def __init__(self, config: Config):
        if not config.GEMINI_API_KEY or "AIza" not in config.GEMINI_API_KEY:
            raise ValueError("A chave da API do Gemini não foi configurada.")
        genai.configure(api_key=config.GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-1.5-flash')
    def responder_pergunta(self, pergunta: str, df_notas: pd.DataFrame):
        if df_notas.empty:
            return "Não há dados de notas fiscais para analisar. Por favor, ajuste os filtros."
        try:
            dados_para_analise = df_notas.head(100).to_csv(index=False, sep=';')
            prompt = f"Você é um assistente fiscal. Responda à pergunta do usuário com base nos dados em CSV abaixo.\n\nDados:\n{dados_para_analise}\n\nPergunta:\n{pergunta}"
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Erro ao chamar a API do Gemini: {e}")
            return f"Ocorreu um erro ao processar sua pergunta: {e}"

class Dashboard:
    def __init__(self):
        self.config = Config()
        self.db_manager = DatabaseManager(self.config)
        st.set_page_config(page_title="Gestor Fiscal AI", layout="wide", initial_sidebar_state="expanded")

    def run(self):
        st_autorefresh(interval=600000, key="datarefresher") # Atualiza a cada 10 minutos
        st.sidebar.title("Gestor Fiscal AI 🤖")
        st.sidebar.header("Filtros de Período")
        self.data_inicio = st.sidebar.date_input("Data Início", datetime.now() - timedelta(days=30))
        self.data_fim = st.sidebar.date_input("Data Fim", datetime.now())
        self.carregar_dados()
        tab1, tab2, tab3, tab4 = st.tabs(["📊 Visão Geral", "📄 Análise Detalhada", "💬 Chat Fiscal (Gemini)", "📋 Logs"])
        with tab1: self.render_visao_geral()
        with tab2: self.render_analise_detalhada()
        with tab3: self.render_chat_fiscal()
        with tab4: self.render_logs()

    def carregar_dados(self):
        filtros = {'data_emissao_inicio': self.data_inicio.isoformat(), 'data_emissao_fim': self.data_fim.isoformat()}
        notas_data = self.db_manager.buscar_dados('notas_fiscais', filtros)
        self.df_notas = pd.DataFrame(notas_data) if notas_data else pd.DataFrame()

    def render_visao_geral(self):
        st.header("Visão Geral do Período")
        if self.df_notas.empty: st.warning("Nenhuma nota fiscal encontrada para o período selecionado."); return
        self.df_notas['valor_total'] = pd.to_numeric(self.df_notas['valor_total'], errors='coerce').fillna(0)
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total de Notas", f"{self.df_notas.shape[0]:,}")
        col2.metric("Valor Total", f"R$ {self.df_notas['valor_total'].sum():,.2f}")
        col3.metric("Ticket Médio", f"R$ {self.df_notas['valor_total'].mean():,.2f}")
        col4.metric("Fornecedores Únicos", f"{self.df_notas['cnpj_emitente'].nunique():,}")
        st.markdown("---")
        col1, col2 = st.columns([1, 1])
        with col1:
            st.subheader("Valor por Fornecedor (Top 10)")
            valor_por_fornecedor = self.df_notas.groupby('nome_emitente')['valor_total'].sum().nlargest(10).sort_values()
            fig = px.bar(valor_por_fornecedor, x='valor_total', y=valor_por_fornecedor.index, orientation='h', text_auto='.2s')
            st.plotly_chart(fig, use_container_width=True)
        with col2:
            st.subheader("Evolução Diária de Notas")
            self.df_notas['data_emissao_dt'] = pd.to_datetime(self.df_notas['data_emissao'])
            notas_por_dia = self.df_notas.groupby(self.df_notas['data_emissao_dt'].dt.date)['valor_total'].sum()
            fig = px.line(notas_por_dia, x=notas_por_dia.index, y='valor_total', markers=True)
            st.plotly_chart(fig, use_container_width=True)

    def render_analise_detalhada(self):
        st.header("Análise Detalhada das Notas Fiscais")
        if self.df_notas.empty: st.warning("Nenhuma nota fiscal para exibir."); return
        st.dataframe(self.df_notas, use_container_width=True, hide_index=True)
        st.subheader("Exportar Dados")
        col1, col2 = st.columns(2)
        if col1.button("Exportar para Excel"):
            with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
                self.df_notas.to_excel(tmp.name, index=False)
                st.download_button("Clique para baixar o Excel", data=Path(tmp.name).read_bytes(), file_name="notas_fiscais.xlsx")
        if col2.button("Exportar para CSV"):
             st.download_button("Clique para baixar o CSV", data=self.df_notas.to_csv(index=False, sep=';').encode('utf-8'), file_name="notas_fiscais.csv", mime='text/csv')

    def render_chat_fiscal(self):
        st.header("Converse com seus Dados Fiscais")
        if not self.config.GEMINI_API_KEY or "AIza" not in self.config.GEMINI_API_KEY:
            st.error("A chave da API do Gemini não foi configurada."); return
        try: gemini_chat = GeminiChat(self.config)
        except ValueError as e: st.error(str(e)); return
        if "messages" not in st.session_state: st.session_state.messages = []
        for message in st.session_state.messages:
            with st.chat_message(message["role"]): st.markdown(message["content"])
        if prompt := st.chat_input("Qual o fornecedor com maior valor?"):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"): st.markdown(prompt)
            with st.chat_message("assistant"):
                with st.spinner("Analisando..."):
                    response = gemini_chat.responder_pergunta(prompt, self.df_notas)
                    st.markdown(response)
            st.session_state.messages.append({"role": "assistant", "content": response})

    def render_logs(self):
        st.header("Logs de Processamento")
        logs_data = self.db_manager.buscar_dados('logs_processamento')
        if not logs_data: st.info("Nenhum log de processamento encontrado."); return
        df_logs = pd.DataFrame(logs_data)
        st.dataframe(df_logs, use_container_width=True, hide_index=True)

if __name__ == "__main__":
    try:
        dashboard = Dashboard()
        dashboard.run()
    except Exception as e:
        logger.critical(f"A aplicação principal falhou: {e}")
        st.error(f"Ocorreu um erro crítico na aplicação. Verifique os logs.")