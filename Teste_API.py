import requests

BASE_URL = "http://localhost:8000"
SESSION_ID = "teste-1234"

def main_menu():
    print("\nSelecione uma opção:")
    print("1 - Buscando emprego")
    print("2 - Buscando meu pedido")
    print("3 - Contratar um serviço da Grinstore")
    print("4 - [Digitar outra pergunta]")

def run_test():
    main_menu()
    opcao = input("Escolha uma opção (1-4): ").strip()

    opcoes_menu = {
        "1": "Estou procurando um emprego na área.",
        "2": "Gostaria de saber sobre o status do meu pedido.",
        "3": "Quero contratar um serviço da Grinstore."
    }

    if opcao in opcoes_menu:
        user_query = opcoes_menu[opcao]
    else:
        user_query = input("Digite sua pergunta: ").strip()

    # Faz a classificação inicial
    classify_url = f"{BASE_URL}/classificar"
    classify_data = {"query": user_query, "session_id": SESSION_ID}
    response = requests.post(classify_url, json=classify_data)
    
    # Tenta fazer parse do JSON
    try:
        classify_result = response.json()
    except Exception as e:
        print("Erro ao interpretar resposta da API:", e)
        print("Resposta bruta:", response.text)
        return

    print("\nClassificação da Pergunta (resposta completa):", classify_result)

    if "destination" not in classify_result:
        print("Erro: A API não retornou uma classificação válida!")
        return

    destination = classify_result.get("destination")

    if destination == "emprego":
        print("Classificado como 'emprego'.")
        print("Oi! Que bom que você quer fazer parte do time!")
        print("Envie seu currículo para rh@grinstore.com.br.")

    elif destination == "pedido":
        print("Classificado como 'pedido'.")
        print("A Grinstore não realiza entregas. Tente contato com a loja onde fez a compra.")

    elif destination == "servicos":
        print("Classificado como 'servicos'. Iniciando coleta de informações...")
        next_question = classify_result.get("next_question")
        next_field = classify_result.get("field")

        # Enquanto houver perguntas obrigatórias
        while next_field:
            user_answer = input(f"{next_question} ({next_field}): ").strip()

            answer_url = f"{BASE_URL}/responder"
            answer_data = {
                "session_id": SESSION_ID,
                "field": next_field,
                "answer": user_answer
            }
            r = requests.post(answer_url, json=answer_data)
            answer_result = r.json()
            print("Resposta da API:", answer_result)

            if answer_result.get("message") == "Todas as informações foram coletadas.":
                print("Todas as informações foram coletadas com sucesso!")
                print("Dados Finalizados:", answer_result.get("dados_coletados"))
                break

            # Atualiza a próxima pergunta
            next_question = answer_result.get("next_question")
            next_field = answer_result.get("field")

    elif destination == "resposta":
        # Aqui a API retorna algo como {"destination": "resposta", "answer": "...", "next_question": "..."}
        print("Classificado como 'resposta'.")
        answer = classify_result.get("answer", "")
        next_question = classify_result.get("next_question")

        print(f"Resposta direta da API: {answer}")

        # Se o modelo tiver retornado uma pergunta de follow-up, exiba
        if next_question:
            print(f"Pergunta de follow-up sugerida: {next_question}")
            # Aqui você decide se quer perguntar ao usuário se deseja responder a esse follow-up
            opc = input("Deseja responder a essa pergunta de follow-up? (s/n): ").strip().lower()
            if opc == "s":
                user_followup_answer = input("Digite sua resposta: ").strip()
                # Opcional: você pode reenviar esse follow-up para /classificar, /chat etc.
                # Exemplo simples: chamar /classificar de novo
                followup_data = {"query": user_followup_answer, "session_id": SESSION_ID}
                resp2 = requests.post(classify_url, json=followup_data)
                result2 = resp2.json()
                print("\nNova classificação de follow-up:", result2)
            else:
                print("Ok, finalizando.")

    else:
        print("A resposta não se encaixa nas categorias conhecidas.")


if __name__ == "__main__":
    run_test()
