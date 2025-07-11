import gradio as gr
import websocket
import json
import threading
import time
import queue
from typing import List, Tuple

class WebSocketClient:
    def __init__(self):
        self.ws = None
        self.is_connected = False
        self.server_url = "ws://localhost:8000/ws"
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 2
        self.response_queue = queue.Queue()
        self.is_receiving = False
        
    def on_message(self, ws, message):
        """메시지 수신 콜백"""
        try:
            data = json.loads(message)
            self.response_queue.put(data)
        except json.JSONDecodeError:
            print(f"❌ JSON 파싱 오류: {message}")
    
    def on_error(self, ws, error):
        """오류 발생 콜백"""
        print(f"❌ WebSocket 오류: {error}")
        self.is_connected = False
    
    def on_close(self, ws, close_status_code, close_msg):
        """연결 종료 콜백"""
        print("🔌 WebSocket 연결 종료")
        self.is_connected = False
    
    def on_open(self, ws):
        """연결 성공 콜백"""
        print("✅ WebSocket 연결 성공")
        self.is_connected = True
        self.reconnect_attempts = 0
    
    def connect(self):
        """WebSocket 서버에 연결"""
        try:
            self.ws = websocket.WebSocketApp(
                self.server_url,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close,
                on_open=self.on_open
            )
            
            # 백그라운드 스레드에서 실행
            self.ws_thread = threading.Thread(target=self.ws.run_forever, daemon=True)
            self.ws_thread.start()
            
            # 연결 대기
            for _ in range(50):  # 5초 대기
                if self.is_connected:
                    return True
                time.sleep(0.1)
            
            return False
            
        except Exception as e:
            print(f"❌ WebSocket 연결 실패: {e}")
            self.is_connected = False
            return False
    
    def disconnect(self):
        """WebSocket 연결 종료"""
        if self.ws:
            self.ws.close()
            self.is_connected = False
    
    def send_message(self, message_data):
        """메시지 전송"""
        if not self.is_connected or not self.ws:
            print("❌ WebSocket이 연결되지 않았습니다.")
            return False
        
        try:
            self.ws.send(json.dumps(message_data, ensure_ascii=False))
            return True
        except Exception as e:
            print(f"❌ 메시지 전송 오류: {e}")
            self.is_connected = False
            return False
    
    def receive_response(self):
        """응답 수신 (스트리밍)"""
        if not self.is_connected:
            yield "❌ WebSocket이 연결되지 않았습니다."
            return
        
        response = ""
        timeout = 30  # 30초 타임아웃
        start_time = time.time()
        
        while True:
            try:
                # 큐에서 메시지 수신 (타임아웃 포함)
                data = self.response_queue.get(timeout=1)
                
                if "chunk" in data:
                    # 스트리밍 chunk 처리
                    response += data["chunk"]
                    yield data["chunk"]
                
                elif "done" in data and data["done"]:
                    # 응답 완료
                    print("✅ 응답 완료")
                    break
                
                elif "error" in data:
                    # 오류 처리
                    print(f"❌ 서버 오류: {data['error']}")
                    yield f"\n❌ 오류: {data['error']}"
                    break
                
                # 타임아웃 체크
                if time.time() - start_time > timeout:
                    print("⏰ 응답 타임아웃")
                    yield "\n⏰ 응답 시간 초과"
                    break
                    
            except queue.Empty:
                # 큐가 비어있을 때 연결 상태 확인
                if not self.is_connected:
                    yield "\n❌ 연결이 끊어졌습니다."
                    break
                
                # 타임아웃 체크
                if time.time() - start_time > timeout:
                    print("⏰ 응답 타임아웃")
                    yield "\n⏰ 응답 시간 초과"
                    break
                    
                continue
            
            except Exception as e:
                print(f"❌ 응답 수신 오류: {e}")
                yield f"\n❌ 오류: {e}"
                break

# 전역 WebSocket 클라이언트 인스턴스
ws_client = WebSocketClient()

def send_message_to_server(message, model, temperature, system, user_id, history):
    """서버로 메시지 전송하고 응답 수신"""
    # 연결 확인 및 재연결
    if not ws_client.is_connected:
        print("🔄 WebSocket 연결 시도 중...")
        if not ws_client.connect():
            return history + [(message, "❌ 서버 연결 실패")]
    
    # 메시지 데이터 구성
    message_data = {
        "model": model,
        "temperature": str(temperature),
        "system": system,
        "user_input": message,
        "user_id": user_id if user_id.strip() else "anonymous"
    }
    
    # 메시지 전송
    if not ws_client.send_message(message_data):
        return history + [(message, "❌ 메시지 전송 실패")]
    
    # 사용자 메시지를 히스토리에 추가
    history.append((message, ""))
    
    # 스트리밍 응답 수신
    response = ""
    for chunk in ws_client.receive_response():
        response += chunk
        # 히스토리 업데이트 (실시간 스트리밍 표시)
        history[-1] = (message, response)
    
    return history

def connect_to_server():
    """서버 연결 함수"""
    if ws_client.is_connected:
        return "✅ 이미 연결되어 있습니다."
    
    if ws_client.connect():
        return "✅ 서버에 연결되었습니다."
    else:
        return "❌ 서버 연결에 실패했습니다."

def disconnect_from_server():
    """서버 연결 종료 함수"""
    if ws_client.is_connected:
        ws_client.disconnect()
        return "🔌 서버 연결이 종료되었습니다."
    else:
        return "❌ 연결되지 않은 상태입니다."

# Gradio 인터페이스 생성
def create_interface():
    """Gradio 인터페이스 생성"""
    
    def chat_interface(message, model, temperature, system, user_id, history):
        """채팅 인터페이스 함수"""
        if not message.strip():
            return history, ""
        
        try:
            # 메시지 전송 및 응답 수신
            updated_history = send_message_to_server(message, model, temperature, system, user_id, history)
            return updated_history, ""
        
        except Exception as e:
            print(f"❌ 채팅 인터페이스 오류: {e}")
            return history + [(message, f"❌ 오류 발생: {e}")], ""
    
    with gr.Blocks(title="WebSocket 채팅 테스트") as demo:
        gr.Markdown("# 🚀 WebSocket 채팅 테스트")
        gr.Markdown("유니티와 통신하기 위한 WebSocket 서버 테스트용 채팅 인터페이스")
        
        with gr.Row():
            with gr.Column(scale=1):
                # 연결 상태 및 제어
                gr.Markdown("## 🔌 연결 제어")
                connect_btn = gr.Button("🔗 서버 연결", variant="primary")
                disconnect_btn = gr.Button("🔌 연결 종료", variant="secondary")
                connection_status = gr.Textbox(label="연결 상태", interactive=False)
                
                # 설정 패널
                gr.Markdown("## ⚙️ 설정")
                user_id_input = gr.Textbox(
                    value="user001",
                    label="사용자 ID",
                    placeholder="사용자 ID를 입력하세요...",
                    lines=1
                )
                model_dropdown = gr.Dropdown(
                    choices=["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo", "gpt-4.1"],
                    value="gpt-4o",
                    label="모델 선택"
                )
                temperature_slider = gr.Slider(
                    minimum=0.0, maximum=2.0, value=0.3, step=0.1,
                    label="Temperature"
                )
                system_textbox = gr.Textbox(
                    value="INFJ  | 따뜻한 선생님\n내담자의 감정을 잘 파악하고 섬세하게 공감하며 간결하게 말해주세요.\n질문은 한 가지씩만 합니다.\n항상 질문할 필요 없습니다. 상황에 따라 공감표현만 하는 경우도 있습니다.\n대화가 시작되면 밝은 톤으로 인사해주세요.\n이어서 이름을 물어보고, 대화에서 이름을 불러주세요.\n첫 질문은 이름을 묻는 질문만 하세요.\n\n역할 설정\n당신은 서윤이라는 이름의 따뜻하고 유머 있는 30대 후반 한국인 여성입니다.\n상담심리학 박사이며, 상담심리사 자격을 보유하고 있습니다.\n10년 이상의 상담 경력을 지닌 전문가로, 인간중심 상담 이론을 기반으로 상담을 진행하며, 인지행동치료(CBT) 기법을 보조적으로 사용합니다.\n상담심리사 윤리강령을 준수하며, 상담의 모든 흐름에서 내담자의 자율성과 선택권을 최우선으로 존중합니다.\n당신은 내담자의 말 속에 담긴 정서, 신체감각, 반복되는 인지적 패턴을 파악하며, 맥락 중심의 공감적 반응을 통해 상담을 진행합니다.\n\n상담 기법 및 말투\n주 사용 이론: 인간중심 상담\n보조 사용 이론: 인지행동치료(CBT)\n응답은 반드시 1문장으로 간결하게, 공감과 질문 중심으로 구성합니다.\n내담자가 더 말할 수 있도록, 개방형 질문, 명료화, 감정 반영, **초점화(Focusing)**를 사용합니다.\n말투는 따뜻하고 캐주얼한 존댓말입니다. 단, 상담자 톤은 유지하며 내담자의 표현 스타일을 부드럽게 반영합니다.\n같은 말투, 어휘, 문장 구조를 반복하지 않고, 매 응답마다 새로운 방식의 표현을 사용하세요.\n\n상담 시작\n첫 인사 후, 이름을 물어 기억합니다.\n단, “저는 따뜻한 선생님 서윤이에요” 와 같은 자기소개는 하지 않는다.\n다음 질문을 통해 상담 목표와 호소 문제를 탐색하세요:\n“오늘 상담에서 꼭 다뤄보고 싶은 고민이 있으실까요?”\n“상담을 통해 어떤 점이 조금이라도 나아지면 좋겠다고 느끼셨나요?”\n\n상담 본 과정: 3단계 구조\n단계\t목표\t기법\n1단계. 탐색\t감정, 생각, 경험을 안전하게 표현하도록 돕고, 반복되는 문제의 핵심을 찾아냅니다.\t감정 반영, 명료화, 개방형 질문, 맥락 탐색, 신체감각 확인 등\n2단계. 통찰\t사고-감정-행동의 연결 고리를 인식하고, 반복되는 패턴을 알아차릴 수 있도록 돕습니다.\t감정-사고 연결 질문, 자동사고 탐색, 사고의 기원 탐색, 재구성 유도 등\n3단계. 실행\t실천 가능한 변화와 자기돌봄 행동을 함께 계획합니다.\t준비도 확인, 구체적 실천안 제시, 작은 변화 행동 설계 등\n각 단계가 끝날 때까지 다음 단계로 넘어가지 마세요.\n\n탐색 단계 질문 참고 예시\n(1단계에서 아래 질문 중 상황에 맞는 항목을 참고하여 상황에 맞게 변경하여 활용하세요.)\n요즘 마음이 가장 무거워지는 순간은 언제인가요?\n최근 가장 강하게 느꼈던 감정은 무엇이었나요?\n그 상황에서 어떤 생각이 가장 먼저 떠올랐나요?\n오늘 상담을 통해 가장 다루고 싶은 고민은 무엇인가요?\n\n위기 대응 원칙\n자살, 자해, 폭력 등 위기 표현이 감지되면 즉시 아래 순서로 대응합니다:\n감정적 지지 제공 (\"그 말이 너무 마음에 남아요. 지금 그 마음이 정말 무겁게 느껴지네요.\")\n안전 여부 확인 (\"혹시 지금은 안전한 공간에 계신가요?\")\n전문가 연결 안내 (\"이 감정은 혼자 감당하지 않으셔도 됩니다. 전문가의 도움이 필요할 수 있어요.\")\n진단, 약물, 처방에 대한 언급은 절대 하지 않습니다.\n\n상담 종료 시 반드시 포함할 절차\n따뜻한 인사로 마무리합니다.\n하루에 적용할 수 있는 자기돌봄 과제 중 하나를 제안합니다.\n예시 문장: “오늘 하루를 돌아보며, 감정을 단어로 적어보는 건 어떨까요?”\n“좋아하는 음악을 3분간 조용히 들어보는 건 어때요?”\n“핸드폰 없이 10분 산책하며 생각을 흘려보는 시간을 가져보세요.”\n“지금 떠오르는 고마웠던 일 3가지를 적어볼까요?”\n\n한 줄 소감 유도\n“오늘 상담에서 기억에 남는 말이나, 지금 떠오르는 느낌이 있다면 한 줄로 정리해볼 수 있을까요?”\n“이 시간 이후, 나에게 따뜻하게 해주고 싶은 말이 있다면 어떤 걸까요?”\n\n경고 (AI 상담사 한계 고지)\n당신은 실제 상담사를 대체하지 않습니다.\n당신은 감정 탐색, 자기이해 촉진, 정서적 지지 제공, 효율적인 상담 연계를 위한 역할만 수행합니다.\n진단, 약물, 의학적 조치 등은 다루지 않으며, 위기 시 즉시 전문가와의 연결을 유도합니다.",
                    label="시스템 메시지",
                    lines=3
                )
            
            with gr.Column(scale=2):
                # 채팅 인터페이스
                gr.Markdown("## 💬 채팅")
                chatbot = gr.Chatbot(
                    label="대화 내용",
                    height=500,
                    show_copy_button=True
                )
                
                with gr.Row():
                    message_input = gr.Textbox(
                        label="메시지 입력 (Enter로 전송)",
                        placeholder="메시지를 입력하고 Enter를 누르세요...",
                        lines=1,
                        max_lines=1,
                        scale=4
                    )
                    send_btn = gr.Button("📤 전송", variant="primary", scale=1)
                
                # 사용법 안내
                gr.Markdown("💡 **사용법**: 메시지 입력 후 `Enter`키를 누르거나 `전송` 버튼을 클릭하세요.")
                
                clear_btn = gr.Button("🗑️ 대화 내용 지우기", variant="secondary")
        
        # 이벤트 핸들러
        connect_btn.click(
            fn=connect_to_server,
            outputs=connection_status
        )
        
        disconnect_btn.click(
            fn=disconnect_from_server,
            outputs=connection_status
        )
        
        # 전송 버튼 클릭 이벤트
        send_btn.click(
            fn=chat_interface,
            inputs=[message_input, model_dropdown, temperature_slider, system_textbox, user_id_input, chatbot],
            outputs=[chatbot, message_input]
        )
        
        # Enter 키 이벤트
        message_input.submit(
            fn=chat_interface,
            inputs=[message_input, model_dropdown, temperature_slider, system_textbox, user_id_input, chatbot],
            outputs=[chatbot, message_input]
        )
        
        clear_btn.click(
            fn=lambda: [],
            outputs=chatbot
        )
        
        # 시작 시 자동 연결
        demo.load(
            fn=connect_to_server,
            outputs=connection_status
        )
    
    return demo

# 인터페이스 생성 및 실행
if __name__ == "__main__":
    print("🚀 Gradio 채팅 인터페이스 시작")
    print("📍 서버 URL: ws://172.16.10.192:8000/ws")
    print("💡 Enter 키로 메시지 전송 가능")
    
    demo = create_interface()
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        # 언어 설정 추가
        inbrowser=False,
        show_error=True,
        # 또는 환경변수로 설정
)