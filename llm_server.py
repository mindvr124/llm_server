# uvicorn llm_server:app --host 0.0.0.0 --port 8000 (--reload : 선택. 코드 수정 시 자동 서버 재시작)

####################################################################################
# json 양식
# {
#     "model":"gpt-4o",
#     "temperature":"0.3",
#     "system":"당신은 친절하고 공감하는 심리상담가입니다. 질문형 응답을 이어가주세요.",
#     "user_input":"안녕하세요"
# }
####################################################################################

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from langchain.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain.callbacks.streaming_aiter import AsyncIteratorCallbackHandler
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import os
import asyncio
import json

load_dotenv()
app = FastAPI()

# =============================================
# DB 연결 설정
# =============================================
user = os.getenv("DB_USER")
password = os.getenv("DB_PASSWORD")
host = os.getenv("SERVER_HOST")
port = "3306"
database = os.getenv("DB_NAME")

# ==========================================================================================
# MariaDB에 상담 내용 저장
# =========================================================================================
engine = create_engine(
        f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"
    )

# 모든 상담 내용 저장
def save_counsel_history(user_id, user_input, answer):
    query = text("INSERT INTO counsel_history (user_id, user_input, answer) VALUES (:user_id, :user_input, :answer)")
    with engine.begin() as conn:  # 자동 commit 포함
        conn.execute(query, {
            "user_id": user_id,
            "user_input": user_input,
            "answer": answer
        })

# 상담 요약 내용 저장
def save_counsel_summary(user_id, content):
    query = text("INSERT INTO counsel_summary (user_id, content) VALUES (:user_id, :content)")
    with engine.begin() as conn:
        conn.execute(query, {
            "user_id": user_id,
            "content": content
        })

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 메모리 구현, 요약 대화 저장
chat_history = []
summary_text = ""

def get_streaming_llm(model, temperature, callback):
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        streaming=True,
        callbacks=[callback],
        openai_api_key=OPENAI_API_KEY
    )

# =========================
# 요약 생성 함수
# =========================
def generate_summary(llm, history, summary_text):
    summary_template = PromptTemplate(
        input_variables=["history", "summary_text"],
        template="""
        다음은 현재 진행중인 상담의 사용자와 상담사의 대화입니다. 사용자의 이름, 상담 이유와 그에 관련된 핵심 내용을 중복이 없도록 요약해 주세요.
        이전 요약 대화 내용을 포함해서 함께 요약해주세요.
        마무리 인사가 나왔거나, 상담 종료 의사를 밝혔을 때만 마지막에 "[상담 종료]"를 추가해주세요.

        이전 요약 대화:
        {summary_text}
        대화:
        {history}
        """
    )
    summary_chain = summary_template | llm
    return summary_chain.ainvoke({
        "summary_text": summary_text,
        "history": history
    })

# =============================================
# WebSocket 연결 설정
# =============================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("🔌 WebSocket 연결 수락됨")

    user_id = "test_id"
    print("--------------------------------")
    print("test_id 로 상담 내용이 저장됩니다.")
    print("--------------------------------")

    global chat_history, summary_text
    while True:
        try:
            print("📩 메시지 대기 중...")
            data = await websocket.receive_json()

            model = data.get("model")
            temperature = data.get("temperature")
            system = data.get("system")
            user_input = data.get("user_input")

            # if not system or not user_input:
            #     err = json.dumps(
            #         {"error": "system과 user_input 필드는 필수입니다."},
            #         ensure_ascii=False
            #     )
            #     await websocket.send_bytes(err.encode("utf-8"))
            #     continue

            # history 구성 (요약 + 최근 5턴)
            if summary_text:
                recent_history = "\n".join(
                [f"사용자: {item['user']}\n상담사: {item['response']}" for item in chat_history[-5:]]
                )
                history = f"요약된 대화: {summary_text}\n\n최근 대화:\n{recent_history}"
            else:
                history = "\n".join(
                    [f"사용자: {item['user']}\n상담사: {item['response']}" for item in chat_history]
                )

            # 프롬프트 구성
            template = """
            {system}
            '요약된 대화'가 들어오면 인사를 하지 말고 대화를 이어나가주세요.

            이전 대화 :
            {history}

            사용자 메세지 :
            {user_input}
            """
            prompt = PromptTemplate(
                input_variables=["system", "history", "user_input"],
                template=template
            )

            print(f"📨 사용자: {user_input}")
            callback = AsyncIteratorCallbackHandler()
            llm = get_streaming_llm(model, temperature, callback)
            chain = prompt | llm

            response = asyncio.create_task(chain.ainvoke({
                "system": system,
                "history": history,
                "user_input": user_input
            }))

            # chunk 전송
            # async for chunk in callback.aiter():
            #     chunk_bytes = json.dumps({"chunk": chunk}, ensure_ascii=False).encode("utf-8")
            #     await websocket.send_bytes(chunk_bytes)
            async for chunk in callback.aiter(): # local test line
                await websocket.send_json({"chunk": chunk})  # local test line

            response_text = await response
            chat_history.append({
                "user": user_input,
                "response": response_text.content,
            })

            # DB에 상담 내용 저장
            save_counsel_history(user_id, user_input, response_text.content)

            # 상담 내용 요약 (매 5턴)
            if len(chat_history) % 5 == 0:
                full_history = "\n".join(
                    [f"사용자: {item['user']}\n상담사: {item['response']}" for item in chat_history]
                )
                summary_response = await generate_summary(llm, full_history, summary_text)
                summary_text = summary_response.content
                save_counsel_summary(user_id, summary_text)
                print("📝 요약 업데이트:", summary_text)
                chat_history=[]

            # done_msg = json.dumps(
            #     {"done": True, "content": response_text.content},
            #     ensure_ascii=False
            # )
            # await websocket.send_bytes(done_msg.encode("utf-8"))
            # print(f"📨 상담사 응답: {response_text.content}")
            await websocket.send_json({"done": True, "content": response_text.content}) # local test line
            print(f"📨 상담사 응답: {response_text.content}") # local test line

        except WebSocketDisconnect:
            print("🔌 클라이언트가 연결을 종료했습니다.")
            if chat_history:
                full_history = "\n".join(
                    [f"사용자: {item['user']}\n상담사: {item['response']}" for item in chat_history]
                )
                print("🧪 요약 생성 시작")
                summary_response = await generate_summary(llm, full_history)
                summary_text = summary_response.content
                save_counsel_summary(user_id, summary_text)
                print("📄 요약 결과:", summary_response.content)
            break

        except Exception as e:
            print("❌ 처리 중 에러:", e)
            try:
                # error_msg = json.dumps({"error": str(e)}, ensure_ascii=False)
                # await websocket.send_bytes(error_msg.encode("utf-8"))
                await websocket.send_json({"error": str(e)})  # local test line
            except Exception:
                print("❌ 예외 중 예외 발생:", e)
                if chat_history:
                    full_history = "\n".join(
                        [f"사용자: {item['user']}\n상담사: {item['response']}" for item in chat_history]
                    )
                    summary_response = await generate_summary(llm, full_history)
                    summary_text = summary_response.content
                    save_counsel_summary(user_id, summary_text)
            break

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("llm_server:app", host="0.0.0.0", port=port)
