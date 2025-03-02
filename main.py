from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
from dotenv import load_dotenv
from operator import itemgetter
from typing import Literal, Optional, List
from typing_extensions import TypedDict
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

# 📌 Carregar variáveis do ambiente
load_dotenv()  # Agora carrega o .env corretamente
openai_api_key = os.getenv("OPENAI_API_KEY")

if not openai_api_key:
    raise HTTPException(status_code=500, detail="Erro: OPENAI_API_KEY não encontrada!")

# Inicializar FastAPI
app = FastAPI()

# Inicializar modelo OpenAI
llm = ChatOpenAI(model="gpt-4o-mini", openai_api_key=openai_api_key)

# Criar Agente de Roteamento
route_prompt = ChatPromptTemplate.from_template(
    "Classifique a seguinte pergunta: '{query}'. "
    "Retorne um JSON com 'destination' sendo uma das opções: 'emprego', 'pedido', 'servicos'."
)

route_chain = (
    route_prompt
    | llm.with_structured_output(TypedDict("RouteQuery", {"destination": Literal["emprego", "pedido", "servicos"]}))
    | itemgetter("destination")
)

# Criar Agente de Validação Genérica para Perguntas Abertas
validation_prompt = ChatPromptTemplate.from_template(
    "O usuário respondeu '{answer}' para a pergunta '{question}'. "
    "Verifique se a resposta faz sentido no contexto. "
    "Se a resposta for válida, diga apenas 'válido'. "
    "Se não for, explique o conceito da pergunta e forneça exemplos reais."
)

validation_chain = validation_prompt | llm

# Banco de dados temporário para armazenar sessões
session_data = {}

# Perguntas obrigatórias com regras de validação
required_info = [
    ("Atendimento (B2C ou B2B)", "tipo", ["B2C", "B2B", "ia"]),
    ("ERP utilizado", "erp", ["Tiny", "ia"]),
    ("Média de pedidos por mês", "pedidos_mes", "ia"),
    ("Ticket médio", "ticket_medio", "ia"),
    ("Quantidade de SKU", "sku", "ia"),
    ("Email ou telefone para contato", "contato", None)  # Aceita qualquer coisa
]

# Modelos Pydantic para entrada
class QueryRequest(BaseModel):
    query: str
    session_id: str  # Para identificar o usuário

class AnswerRequest(BaseModel):
    session_id: str
    field: str
    answer: str

@app.get("/")
async def home():
    return {"message": "API FastAPI rodando no Azure"}

@app.post("/classificar")
async def classificar_pergunta(data: QueryRequest):
    """
    Recebe uma pergunta e classifica em uma das categorias: 'emprego', 'pedido' ou 'servicos'.
    Se for 'servicos', inicia o processo de coleta de informações.
    """
    try:
        destination = route_chain.invoke({"query": data.query})

        if destination == "servicos":
            # Criar sessão para armazenar respostas
            session_data[data.session_id] = {}
            return {
                "destination": destination,
                "next_question": required_info[0][0],  # Primeira pergunta
                "field": required_info[0][1]
            }

        return {"destination": destination}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def validate_answer(question: str, answer: str, validation_type: Optional[List[str]]) -> dict:
    """
    Valida a resposta com base nos critérios definidos.
    - Se houver uma lista de opções válidas, a resposta deve estar nela.
    - Se precisar de IA, chama o modelo para validar.
    """
    if validation_type:
        if isinstance(validation_type, list):  # Se houver opções válidas
            if answer in validation_type:  
                return {"valid": True, "explanation": f"A resposta '{answer}' é válida."}  # Resposta válida
            elif "ia" in validation_type:  
                # Se "ia" estiver ativado, validar via IA
                validation_result = validation_chain.invoke({"question": question, "answer": answer})
                if "válido" in validation_result.content.lower():
                    return {"valid": True, "explanation": f"A resposta '{answer}' é válida."}
                else:
                    return {"valid": False, "explanation": validation_result.content}

        elif validation_type == "ia":  # Se precisar de IA
            validation_result = validation_chain.invoke({"question": question, "answer": answer})
            if "válido" in validation_result.content.lower():
                return {"valid": True, "explanation": f"A resposta '{answer}' é válida."}
            else:
                return {"valid": False, "explanation": validation_result.content}

    return {"valid": True, "explanation": f"A resposta '{answer}' é válida."}  # Se não precisar de validação, aceita qualquer resposta

@app.post("/responder")
async def responder_pergunta(data: AnswerRequest):
    """
    Recebe respostas do usuário para cada pergunta obrigatória.
    Valida a resposta antes de prosseguir.
    Retorna a próxima pergunta até que todas as informações sejam preenchidas.
    """
    if data.session_id not in session_data:
        raise HTTPException(status_code=400, detail="Sessão não encontrada. Inicie com /classificar.")

    # Descobrir qual pergunta está sendo respondida e sua regra de validação
    for question, field, validation_type in required_info:
        if field == data.field:
            validation_result = validate_answer(question, data.answer, validation_type)
            if not validation_result["valid"]:
                return {
                    "message": f"⚠️ '{data.answer}' pode não estar correto.",
                    "explanation": validation_result["explanation"],
                    "next_question": question,
                    "field": field
                }
            break
    else:
        raise HTTPException(status_code=400, detail=f"Campo '{data.field}' não reconhecido.")

    # Armazenar resposta válida
    session_data[data.session_id][data.field] = data.answer

    # Verificar qual a próxima pergunta
    preenchidos = len(session_data[data.session_id])
    if preenchidos < len(required_info):
        next_question, next_field, _ = required_info[preenchidos]
        return {
            "message": "✅ Resposta aceita!",
            "explanation": validation_result["explanation"],
            "next_question": next_question,
            "field": next_field
        }

    # Se todas as perguntas foram respondidas
    return {
        "message": "✅ Todas as informações foram coletadas!",
        "dados_coletados": session_data[data.session_id]
    }

# Para rodar no Azure App Service
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
