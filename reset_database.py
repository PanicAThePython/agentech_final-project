# Nome do arquivo: reset_database.py
# Finalidade: Apagar e recriar todas as tabelas no banco de dados da Render.com.

import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, exc

def reset_database_schema():
    """
    Carrega as configurações, pede confirmação e executa os scripts
    para apagar e recriar as tabelas do banco de dados.
    """
    # Carrega a DATABASE_URL do arquivo .env
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        print("\n❌ ERRO: A variável DATABASE_URL não foi encontrada no seu arquivo .env.")
        print("Por favor, verifique se o arquivo .env existe e contém a URL de conexão.")
        sys.exit(1)

    # --- AVISO DE SEGURANÇA ---
    # Esta é uma etapa crucial para evitar a perda acidental de dados.
    print("="*60)
    print("--- AVISO DE SEGURANÇA ---")
    print("Este script irá APAGAR PERMANENTEMENTE todas as tabelas e dados")
    print("do banco de dados configurado em sua DATABASE_URL.")
    print("\nTabelas afetadas: 'itens_nota_fiscal', 'notas_fiscais', 'logs_processamento'.")
    print("\nEsta ação NÃO PODE SER DESFEITA.")
    print("="*60)

    try:
        confirmacao = input("\n> Para confirmar a operação, digite 'SIM' e pressione Enter: ")
        if confirmacao.strip().upper() != 'SIM':
            print("\nOperação cancelada pelo usuário. Nenhuma alteração foi feita.")
            sys.exit(0)
    except KeyboardInterrupt:
        print("\nOperação cancelada pelo usuário.")
        sys.exit(0)

    # Comandos SQL para apagar as tabelas antigas.
    # O 'CASCADE' garante que as dependências (foreign keys) sejam removidas corretamente.
    drop_sql = """
    DROP TABLE IF EXISTS itens_nota_fiscal CASCADE;
    DROP TABLE IF EXISTS notas_fiscais CASCADE;
    DROP TABLE IF EXISTS logs_processamento CASCADE;
    """

    # Comandos SQL para criar as novas tabelas (estrutura correta)
    create_sql = """
    CREATE TABLE logs_processamento (
        id SERIAL PRIMARY KEY,
        timestamp TIMESTAMP WITH TIME ZONE DEFAULT now(),
        tipo_operacao VARCHAR(50),
        arquivo_processado VARCHAR(255),
        status VARCHAR(50),
        mensagem_erro TEXT
    );

    CREATE TABLE notas_fiscais (
        id SERIAL PRIMARY KEY,
        numero VARCHAR(50) NOT NULL,
        serie VARCHAR(10) NOT NULL,
        data_emissao TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        cnpj_emitente VARCHAR(50) NOT NULL,
        nome_emitente VARCHAR(255),
        valor_total NUMERIC(15, 2) NOT NULL,
        chave_acesso VARCHAR(60) UNIQUE NOT NULL,
        natureza_operacao VARCHAR(255),
        situacao VARCHAR(50) DEFAULT 'Pendente',
        data_vencimento TIMESTAMP WITHOUT TIME ZONE,
        cnpj_destinatario VARCHAR(50),
        nome_destinatario VARCHAR(255),
        valor_icms NUMERIC(15, 2) DEFAULT 0.0,
        valor_ipi NUMERIC(15, 2) DEFAULT 0.0,
        valor_pis NUMERIC(15, 2) DEFAULT 0.0,
        valor_cofins NUMERIC(15, 2) DEFAULT 0.0,
        xml_original TEXT,
        processado_em TIMESTAMP WITH TIME ZONE DEFAULT now()
    );

    CREATE TABLE itens_nota_fiscal (
        id SERIAL PRIMARY KEY,
        nota_fiscal_id INTEGER NOT NULL REFERENCES notas_fiscais(id) ON DELETE CASCADE,
        codigo VARCHAR(100),
        descricao TEXT,
        ncm VARCHAR(20),
        quantidade NUMERIC(15, 5),
        valor_unitario NUMERIC(15, 5),
        valor_total NUMERIC(15, 2)
    );

    CREATE INDEX idx_nf_data_emissao ON notas_fiscais(data_emissao);
    CREATE INDEX idx_nf_cnpj_emitente ON notas_fiscais(cnpj_emitente);
    """

    try:
        print("\nConectando ao banco de dados...")
        engine = create_engine(database_url)
        
        # .begin() garante que todas as operações ocorram em uma única transação.
        # Se algo falhar, tudo é desfeito (rollback).
        with engine.begin() as connection:
            print("  -> Conexão bem-sucedida.")
            
            print("  -> 1. Apagando tabelas antigas (se existirem)...")
            connection.execute(text(drop_sql))
            
            print("  -> 2. Criando novas tabelas...")
            connection.execute(text(create_sql))
        
        print("\n✅ SUCESSO! O banco de dados foi resetado.")
        print("As tabelas foram apagadas e recriadas com a estrutura correta.")

    except exc.SQLAlchemyError as e:
        print(f"\n❌ ERRO DE BANCO DE DADOS: Não foi possível completar a operação.")
        print(f"   Por favor, verifique se sua DATABASE_URL no arquivo .env está correta.")
        print(f"   Detalhes do erro: {e}")
    except Exception as e:
        print(f"\n❌ Ocorreu um erro inesperado: {e}")

if __name__ == "__main__":
    reset_database_schema()