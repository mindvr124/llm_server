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

from fastapi import FastAPI, WebSocket
from langchain.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langchain.callbacks.streaming_aiter import AsyncIteratorCallbackHandler
from dotenv import load_dotenv
import os
import asyncio

load_dotenv()
app = FastAPI()

def get_streaming_llm(model, temperature, callback):
    api_key = os.getenv("OPENAI_API_KEY")
    print("🔑 API 키:", api_key)
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        streaming=True,
        callbacks=[callback],
        openai_api_key=api_key  # ✅ 이 줄이 꼭 있어야 함
    )

# 히스토리 저장 변수 (서버 실행 중 유지됨)
chat_history = []

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("ChatOpenAI 실제 클래스:", ChatOpenAI.__module__)
    print("🔌 WebSocket 연결 수락됨")

    while True:
        try:
            print("📩 메시지 대기 중...")
            data = await websocket.receive_json()

            model = data.get("model")
            temperature = data.get("temperature")
            system = data.get("system")
            user_input = data.get("user_input")
            

            if not system or not user_input:
                await websocket.send_json({"error": "template and user_input are required"})
                continue
            
            history = "\n".join(
                [f"사용자: {item['user']}\n상담사: {item['response']}" for item in chat_history]
            )

            template = """
            {system}

            이전 대화 :
            {history}

            사용자 메세지 :
            {user_input}
            """

            # 프롬프트 템플릿
            prompt = PromptTemplate(
                input_variables=["system", "history", "user_input"],
                template=template
            )
            print(f"📨 사용자: {user_input}")
            callback = AsyncIteratorCallbackHandler()
            llm = get_streaming_llm(model, temperature, callback)

            # 최신 LangChain 구조: RunnableSequence
            chain = prompt | llm

            # Streaming 실행
            response = asyncio.create_task(chain.ainvoke({
                "system": system,
                "history": history,
                "user_input": user_input
            }))

            # 스트리밍 응답 전송
            async for chunk in callback.aiter():
                await websocket.send_json({"chunk": chunk})

            # 결과 대기
            response_text = await response
            # 응답을 메모리에 저장
            chat_history.append({
                "user": user_input,
                "response": response_text.content,
            })

            await websocket.send_json({"done": True, "content":response_text.content})
            print(f"📨 상담사: {response_text.content}")
        except Exception as e:
            await websocket.send_json({"error": str(e)})
            print("❌ 에러 발생:", e)
            break
