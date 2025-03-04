import re
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import json
import pyodbc
from dotenv import load_dotenv
from operator import itemgetter
from typing import Literal, Optional, List, Union
from typing_extensions import TypedDict
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain.schema import SystemMessage, HumanMessage, AIMessage

# Carregar variáveis de ambiente
load_dotenv()
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    raise HTTPException(status_code=500, detail="Erro: OPENAI_API_KEY não encontrada!")

# Configuração do banco de dados
DB_SERVER = "192.168.4.26"
DB_DATABASE = "Mobifyme"
DB_USERNAME = "ezcony"
DB_PASSWORD = "ezc1826"

def get_db_connection():
    conn = pyodbc.connect(
        f"DRIVER={{SQL Server}};SERVER={DB_SERVER};DATABASE={DB_DATABASE};UID={DB_USERNAME};PWD={DB_PASSWORD}"
    )
    return conn

# Funções para persistência da sessão "servicos"
def salvar_sessao(session_id: str, data: dict):
    conn = get_db_connection()
    cursor = conn.cursor()
    json_data = json.dumps(data)
    cursor.execute(
        """
        MERGE INTO AISA.TABELA_SESSAO AS target
        USING (SELECT ? AS session_id, ? AS data) AS source
        ON target.session_id = source.session_id
        WHEN MATCHED THEN
            UPDATE SET data = source.data
        WHEN NOT MATCHED THEN
            INSERT (session_id, data) VALUES (source.session_id, source.data);
        """, 
        (session_id, json_data)
    )
    conn.commit()
    conn.close()

def carregar_sessao(session_id: str) -> dict:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT data FROM AISA.TABELA_SESSAO WHERE session_id = ?", (session_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    else:
        return None

# Funções para persistência do histórico de chat
def salvar_historico_chat(session_id: str, messages: List[dict]):
    conn = get_db_connection()
    cursor = conn.cursor()
    json_data = json.dumps(messages)
    cursor.execute(
        """
        MERGE INTO AISA.TABELA_SESSAO_CHAT AS target
        USING (SELECT ? AS session_id, ? AS data) AS source
        ON target.session_id = source.session_id
        WHEN MATCHED THEN
            UPDATE SET data = source.data
        WHEN NOT MATCHED THEN
            INSERT (session_id, data) VALUES (source.session_id, source.data);
        """,
        (session_id, json_data)
    )
    conn.commit()
    conn.close()

def carregar_historico_chat(session_id: str) -> List[dict]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT data FROM AISA.TABELA_SESSAO_CHAT WHERE session_id = ?", (session_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return json.loads(row[0])
    else:
        return [
            {
                "role": "system",
                "content": "Você está conversando com o sistema da Grinstore. Em que posso ajudar?"
            }
        ]

# ---------------------------------------------
# Definição dos modelos Pydantic
# ---------------------------------------------
class QueryRequest(BaseModel):
    query: str
    session_id: str

class AnswerRequest(BaseModel):
    session_id: str
    field: str
    answer: str

class ChatRequest(BaseModel):
    session_id: str
    user_message: str

# Inicializar FastAPI
app = FastAPI()

# Inicializar modelo OpenAI
llm = ChatOpenAI(model="ft:gpt-4o-mini-2024-07-18:grinstore:cwvendedor:AwCAK35P", openai_api_key=openai_api_key)

# ---------------------------------------------
# 1) Agente de Roteamento
# ---------------------------------------------
route_prompt = ChatPromptTemplate.from_template(
    """Você é um classificador de intenções para a Grinstore. 

Exemplos:
1) Pergunta: "Estou procurando uma vaga de trabalho." 
   Resposta: {{"destination": "emprego"}}

2) Pergunta: "Quero acompanhar o status do meu pedido." 
   Resposta: {{"destination": "pedido"}}

3) Pergunta: "Quero contratar um serviço da Grinstore." 
   Resposta: {{"destination": "servicos"}}

4) Pergunta: "Qual a capital do Brasil?" 
   Resposta: {{"destination": "resposta"}}

Regras:
- Se o usuário falar sobre emprego (vagas, currículos etc.), classifique como "emprego".
- Se o usuário falar sobre pedidos (status, entrega etc.), classifique como "pedido".
- Se o usuário falar sobre contratar serviços, consultoria, soluções, classifique como "servicos".
- Caso contrário, classifique como "resposta".

Classifique a seguinte pergunta: '{query}'.
Retorne estritamente um JSON com "destination" = "emprego", "pedido", "servicos" ou "resposta"."""
)


RouteDict = TypedDict("RouteQuery", {"destination": Literal["emprego", "pedido", "servicos", "resposta"]})
route_chain = route_prompt | llm.with_structured_output(RouteDict) | itemgetter("destination")

# ---------------------------------------------
# 2) Agente de Resposta Direta (fora do escopo)
# ---------------------------------------------
answer_prompt = ChatPromptTemplate.from_template(
    """Você é a IA da Grinstore. Responda a seguinte pergunta de forma clara e objetiva: '{query}'.
Retorne um JSON estrito no formato:
{{
  "answer": "...",
  "next_question": "Quer saber mais algo sobre nossos serviços?"
}}"""
)

AnswerWithFollowup = TypedDict("AnswerWithFollowup", {"answer": str, "next_question": str})
answer_chain = answer_prompt | llm.with_structured_output(AnswerWithFollowup)

# ---------------------------------------------
# 3) Cadeia de Validação Estruturada
# ---------------------------------------------
ValidationOutput = TypedDict("ValidationOutput", {"valid": bool, "explanation": str})
validation_prompt = ChatPromptTemplate.from_template(
    "O usuário respondeu '{answer}' para a pergunta '{question}'. "
    "Retorne um JSON com 'valid' (true/false) e 'explanation' (string)."
)
validation_chain = validation_prompt | llm.with_structured_output(ValidationOutput)

# ---------------------------------------------
# 4) Perguntas obrigatórias para fluxo "servicos"
# ---------------------------------------------
required_info = [
    ("Atendimento (B2C ou B2B)", "tipo", ["B2C", "B2B", "ia"], "string"),
    ("ERP utilizado", "erp", ["Tiny", "ia"], "string"),
    ("Média de pedidos por mês", "pedidos_mes", "ia", "Numeric"),
    ("Ticket médio", "ticket_medio", "ia", "Numeric"),
    ("Quantidade de SKU", "sku", "ia", "Numeric"),
    ("Email ou telefone para contato", "contato", None, [r"^[^@]+@[^@]+\.[^@]+$", r"^(?=.*\d)[+()\-.\s0-9]{7,}$"])
]

# ---------------------------------------------
# Rota Inicial
# ---------------------------------------------
@app.get("/")
async def home():
    return {"message": "API FastAPI rodando no Azure"}

# ---------------------------------------------
# /classificar
# ---------------------------------------------
@app.post("/classificar")
async def classificar_pergunta(data: QueryRequest):
    try:
        destination = route_chain.invoke({"query": data.query})
        if destination == "servicos":
            session_data = {"destination": "servicos", "current_index": 0, "answers": {}}
            salvar_sessao(data.session_id, session_data)
            return {
                "destination": destination,
                "next_question": required_info[0][0],
                "field": required_info[0][1]
            }
        elif destination == "resposta":
            result = answer_chain.invoke({"query": data.query})
            return {
                "destination": "resposta",
                "answer": result["answer"],
                "next_question": result["next_question"]
            }
        else:
            return {"destination": destination, "message": f"Sua pergunta foi classificada como {destination}."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------
# Função de validação para "servicos"
# ---------------------------------------------
def validate_answer(question: str, answer: str, validation_type: Optional[List[str]], field_name: str, expected_type: Union[str, List[str]]) -> dict:
    answer_stripped = answer.strip()
    if expected_type == "Numeric":
        try:
            int(answer_stripped)
            return {"valid": True, "explanation": f"{answer} é válido."}
        except ValueError:
            return {"valid": False, "explanation": f"{answer} não é um número válido."}
    if isinstance(expected_type, list):
        for pattern in expected_type:
            if re.match(pattern, answer_stripped):
                return {"valid": True, "explanation": "Contato válido."}
        return {"valid": False, "explanation": "Não é um email ou telefone válido."}
    if validation_type:
        lower_ans = answer_stripped.lower()
        if isinstance(validation_type, list):
            valid_opts = [opt.lower() for opt in validation_type]
            if lower_ans in valid_opts:
                return {"valid": True, "explanation": "Opção reconhecida."}
            if "ia" in valid_opts:
                result = validation_chain.invoke({"question": question, "answer": answer})
                return {"valid": result["valid"], "explanation": result["explanation"]}
        elif validation_type == "ia":
            result = validation_chain.invoke({"question": question, "answer": answer})
            return {"valid": result["valid"], "explanation": result["explanation"]}
    return {"valid": True, "explanation": "Resposta aceita."}

# ---------------------------------------------
# /responder (fluxo servicos)
# ---------------------------------------------
@app.post("/responder")
async def responder_pergunta(data: AnswerRequest):
    session_data = carregar_sessao(data.session_id)
    if not session_data:
        raise HTTPException(status_code=400, detail="Sessão não encontrada. Inicie com /classificar.")
    if session_data.get("destination") != "servicos":
        return {"message": "Este endpoint é apenas para o fluxo de servicos."}

    current_index = session_data.get("current_index", 0)
    if current_index >= len(required_info):
        return {
            "message": "Todas as informações foram coletadas.",
            "dados_coletados": session_data.get("answers", {})
        }
    expected_question, expected_field, validation_type, expected_type = required_info[current_index]
    if data.field != expected_field:
        return {
            "message": f"Campo inesperado. Esperado: {expected_field}.",
            "next_question": expected_question,
            "field": expected_field
        }
    check = validate_answer(expected_question, data.answer, validation_type, data.field, expected_type)
    if not check["valid"]:
        return {
            "message": f"'{data.answer}' não passou na validação.",
            "explanation": check["explanation"],
            "next_question": expected_question,
            "field": expected_field
        }
    session_data["answers"][data.field] = data.answer
    session_data["current_index"] = current_index + 1
    salvar_sessao(data.session_id, session_data)
    if session_data["current_index"] < len(required_info):
        nxt_q, nxt_f, _, _ = required_info[session_data["current_index"]]
        return {
            "message": "Resposta aceita.",
            "next_question": nxt_q,
            "field": nxt_f,
            "dados_coletados": session_data["answers"]
        }
    else:
        return {
            "message": "Todas as informações foram coletadas.",
            "dados_coletados": session_data["answers"]
        }

# ---------------------------------------------
# /chat (fluxo conversacional com histórico)
# ---------------------------------------------
@app.post("/chat")
def chat_endpoint(data: ChatRequest):
    historico = carregar_historico_chat(data.session_id)
    historico.append({"role": "user", "content": data.user_message})
    
    lc_messages = []
    for msg in historico:
        if msg["role"] == "system":
            lc_messages.append(SystemMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            lc_messages.append(AIMessage(content=msg["content"]))
        else:
            lc_messages.append(HumanMessage(content=msg["content"]))
    
    resposta = llm(lc_messages)
    resposta_texto = resposta.content
    historico.append({"role": "assistant", "content": resposta_texto})
    salvar_historico_chat(data.session_id, historico)
    
    return {
        "message": resposta_texto,
        "session_id": data.session_id
    }

# ---------------------------------------------
# Execução via uvicorn
# ---------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
