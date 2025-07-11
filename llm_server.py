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
import uvicorn

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

# =============================================
# MariaDB에 상담 내용 저장
# =============================================
engine = create_engine(
        f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"
    )

# =============================================
# 사용자가 존재하는지 확인
# =============================================
def ensure_user_exists(user_id):
    check_query = text("SELECT COUNT(*) FROM user WHERE user_id = :user_id")
    with engine.begin() as conn:
        result = conn.execute(check_query, {"user_id": user_id})
        count = result.scalar()
        print(f"사용자 ID : {user_id}")

        # 사용자가 없으면 생성
        if count == 0:
            insert_user_query = text("INSERT INTO user (user_id) VALUES (:user_id)")
            conn.execute(insert_user_query, {
                "user_id": user_id
            })
            print(f"새 사용자 생성: {user_id}")

# =============================================
# 모든 상담 내용 저장
# =============================================
def save_counsel_history(user_id, user_input, answer):
    query = text("INSERT INTO counsel_history (user_id, user_input, answer) VALUES (:user_id, :user_input, :answer)")
    with engine.begin() as conn:  # 자동 commit 포함
        conn.execute(query, {
            "user_id": user_id,
            "user_input": user_input,
            "answer": answer
        })

# =============================================
# 요약 생성 함수
# =============================================
def generate_summary(llm, history, summary_text):
    summary_template = PromptTemplate(
        input_variables=["history", "summary_text"],
        template="""
        이전 요약 대화:
        {summary_text}

        -----------------
        다음은 현재 진행중인 상담의 대화 기록입니다. 
        사용자의 이름, 상담 이유와 그에 관련된 핵심 내용을 중복이 없도록 요약해 주세요.
        이전 요약 대화에서 중요한 내용은 삭제하지 말고 덧붙여서 요약해주세요.

        무의미한 대화나 인사만 있는 경우 요약하지 말고 받은 데이터를 그대로 보내주세요.
        -----------------

        실시간 대화:
        {history}
        """
    )
    summary_chain = summary_template | llm
    return summary_chain.ainvoke({
        "summary_text": summary_text,
        "history": history
    })

# =============================================
# 상담 요약 내용 저장
# =============================================
def save_counsel_summary(user_id, content):
    if len(content) < 30:
        return 0
    query = text("INSERT INTO counsel_summary (user_id, content) VALUES (:user_id, :content)")
    with engine.begin() as conn:
        conn.execute(query, {
            "user_id": user_id,
            "content": content
        })

# =============================================
# 지난 상담 요약 내용 조회
# =============================================
def get_counsel_summary(user_id):
    query = text("""
        SELECT content 
        FROM counsel_summary 
        WHERE user_id = :user_id 
        ORDER BY id DESC 
        LIMIT 1
    """)
    with engine.begin() as conn:
        result = conn.execute(query, {"user_id": user_id})
        row = result.fetchone()
        return row[0] if row else None

# =============================================
# 스트리밍 모델 생성
# =============================================
def get_streaming_llm(model, temperature, callback):
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        streaming=True,
        callbacks=[callback],
        openai_api_key=OPENAI_API_KEY
    )

# =============================================
# 사용자 이름 추출 함수
# =============================================
async def extract_user_name(llm, history, user_id):
    check_query = text("SELECT user_name FROM user WHERE user_id = :user_id")
    with engine.begin() as conn:
        result = conn.execute(check_query, {"user_id": user_id})
        row = result.fetchone()

        if row and row[0] not in (None, "", "알수없음"):
            # 이미 이름이 있으면 추출하지 않음
            return None

    # 이름 추출 프롬프트
    name_prompt = PromptTemplate(
        input_variables=["history"],
        template="""
        다음은 사용자와 상담사의 대화입니다.

        이 대화에서 **사용자의 이름**만 추출해 주세요.
        - "저는 김철수예요", "제 이름은 정은지입니다", "은지라고 불러주세요", "준호님,"과 같은 표현을 참고하세요.
        - 반드시 **이름만** 출력하세요. 이름이 없는 경우 "알수없음"이라고 출력하세요.
        - 예시 출력: 김철수 / 은지 / 알수없음

        대화:
        {history}

        사용자 이름:
        """
    )
    chain = name_prompt | llm
    user_name = (await chain.ainvoke({"history": history})).content.strip()

    # 기존 유저라도 이름 업데이트
    update_query = text("UPDATE user SET user_name = :user_name WHERE user_id = :user_id")
    with engine.begin() as conn:
        conn.execute(update_query, {"user_id": user_id, "user_name": user_name})

    return user_name



# =============================================
# WebSocket 연결 설정
# =============================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 메모리 구현, 요약 대화 저장
chat_history = []
summary_text = ""

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("🔌 WebSocket 연결 수락됨")

    global chat_history, summary_text
    while True:
        try:
            print("📩 메시지 대기 중...")
            data = await websocket.receive_json()

            user_id = data.get("user_id")
            ensure_user_exists(user_id)
            print(f"USER_ID: {user_id}")
            print("------------------------")

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

            # 지난 상담 요약 내용 조회
            summary_data = get_counsel_summary(user_id)
            print(f"지난 상담 요약 내용 : {summary_data}")

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
            if(summary_data):
                template = """
                    {system}

                    '요약 대화'는 실시간 요약중인 대화입니다. 인사를 하지 말고 대화를 이어나가주세요.
                    '지난 상담 대화'는 지난 상담 요약 내용입니다. 어떤 변화가 있었는지 물어보며 대화를 이어나가주세요.

                    지난 상담 대화 :
                    {summary_data}

                    요약 대화 :
                    {history}

                    사용자 메세지 :
                    {user_input}
                """
                prompt = PromptTemplate(
                    input_variables=["system", "summary_data", "history", "user_input"],
                    template=template
                )
                print(f"📨 사용자: {user_input}")
                callback = AsyncIteratorCallbackHandler()
                llm = get_streaming_llm(model, temperature, callback)
                chain = prompt | llm

                response = asyncio.create_task(chain.ainvoke({
                    "system": system,
                    "summary_data": summary_data,
                    "history": history,
                    "user_input": user_input
                }))

            # 첫 상담인 사용자
            else: 
                template = """
                {system}
                '요약 대화'는 실시간 요약중인 대화입니다. 인사를 하지 말고 대화를 이어나가주세요.

                요약 대화 :
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
                print("📝 요약 업데이트:", summary_text)
                chat_history=[]

            # done_msg = json.dumps(
            #     {"done": True, "content": response_text.content},
            #     ensure_ascii=False
            # )
            # await websocket.send_bytes(done_msg.encode("utf-8"))
            # print(f"📨 상담사 응답: {response_text.content}")
            await websocket.send_json({"done": True}) # local test line
            print(f"📨 상담사 응답: {response_text.content}") # local test line

        except WebSocketDisconnect:
            print("🔌 클라이언트가 연결을 종료했습니다.")
            if chat_history:
                full_history = "\n".join(
                    [f"사용자: {item['user']}\n상담사: {item['response']}" for item in chat_history]
                )
                print("🧪 요약 생성 시작")
                summary_response = await generate_summary(llm, full_history, summary_text)
                summary_text = summary_response.content
                save_counsel_summary(user_id, summary_text)
                await extract_user_name(llm, summary_text, user_id)
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
                    summary_response = await generate_summary(llm, full_history, summary_text)
                    summary_text = summary_response.content
                    save_counsel_summary(user_id, summary_text)
            break

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("llm_server:app", host="0.0.0.0", port=port)
