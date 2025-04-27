import os
import threading
import time
import math
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from datetime import datetime

import pygame
import pyaudio
import wave
import requests
from openai import OpenAI
from spark_mucl_cn_iat import SpeechRecognizer

# Live2D & OpenGL
from pyopengltk import OpenGLFrame
from OpenGL import GL
import live2d.v3 as live2d
from live2d.v3.params import StandardParams
from live2d.utils.lipsync import WavHandler

class Live2DFrame(OpenGLFrame):
    def initgl(self):
        # 初始化 Live2D 与 OpenGL
        live2d.init()
        live2d.glewInit()
        self.model = live2d.LAppModel()
        self.model.LoadModelJson("./live2D/Resources/chun/chun.model3.json")
        self.model.Resize(self.width, self.height)
        # 口型同步 WAV
        self.wav = WavHandler()
        #self.wav.Start("./live2D/Resources/voice.wav")
        # 动画循环开启
        self.animate = True
        self.mouth_sync_active = False

    def redraw(self):
        # 每帧更新模型与口型
        self.model.Update()
        # 鼠标视线跟踪已在事件中处理
        # 口型同步或模拟
        if self.wav.Update():
            lvl = self.wav.GetRms() * 3
            self.model.SetParameterValue(StandardParams.ParamMouthOpenY, lvl)
        elif self.mouth_sync_active:
            # 模拟口型振动
            lvl = (math.sin(time.time()*10) + 1) / 2 * 0.5
            self.model.SetParameterValue(StandardParams.ParamMouthOpenY, lvl)
        # 清空缓冲并绘制
        live2d.clearBuffer(255, 255, 255, 0)
        self.model.Draw()

    def on_mouse_move(self, event):
        # 在窗口内根据鼠标位置调整视线
        if self.model:
            self.model.Drag(event.x, event.y)

    def start_mouth(self):
        self.mouth_sync_active = True

    def stop_mouth(self):
        self.mouth_sync_active = False

class ChatApplication:
    def __init__(self, master):
        self.master = master
        master.title("genius chat system v5.0")
        master.geometry("1200x600")
        pygame.mixer.init()

        # OpenAI 客户端配置
        self.client = OpenAI(
            api_key="sk-d6220c8f2f784ea3908216eff99d48ee",
            base_url="https://api.deepseek.com"
        )
        # 对话历史
        self.conversation_history = [
            {"role": "system", "content": "你是一个幽默的助手，你的回答幽默且准确并且简短，并且不会带表情符号"}
        ]
        self.is_responding = False
        self.typing_tag = None
        self.audio_format = pyaudio.paInt16
        self.channels = 1
        self.sample_rate = 16000
        self.chunk = 1024
        self.audio_frames = []
        self.is_recording = False
        self.audio_files = {}

        self._build_ui()
        self._configure_styles()

    def _build_ui(self):
        # 主分割窗格
        pane = ttk.Panedwindow(self.master, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)
        # 左侧聊天区
        left = ttk.Frame(pane)
        # 聊天记录
        self.chat_history = scrolledtext.ScrolledText(
            left, wrap=tk.WORD, font=('微软雅黑', 12), state='disabled',
            bg='#FFFFFF', padx=10, pady=10)
        self.chat_history.pack(fill=tk.BOTH, expand=True)
        # 输入区
        input_frame = ttk.Frame(left)
        input_frame.pack(fill=tk.X, pady=5)
        # 录音按钮
        self.record_btn = ttk.Button(
            input_frame, text="recording", command=self.toggle_recording)
        self.record_btn.pack(side=tk.RIGHT, padx=(0,5))
        # 输入框
        self.user_input = ttk.Entry(input_frame, font=('微软雅黑', 12))
        self.user_input.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,5))
        self.user_input.bind("<Return>", lambda e: self.send_message())
        # 发送按钮
        self.send_btn = ttk.Button(input_frame, text="send", command=self.send_message)
        self.send_btn.pack(side=tk.RIGHT)
        pane.add(left, weight=3)
        # 右侧 Live2D 区
        right = ttk.Frame(pane, width=500, height=600)
        right.pack_propagate(False)
        self.live2d_frame = Live2DFrame(right, width=500, height=600)
        self.live2d_frame.pack(fill=tk.BOTH, expand=True)
        # 绑定鼠标移动事件
        self.live2d_frame.bind("<Motion>", self.live2d_frame.on_mouse_move)
        # 设置帧率
        self.live2d_frame.framerate = 60
        pane.add(right, weight=1)

    def _configure_styles(self):
        style = ttk.Style()
        style.configure('Black.TButton', foreground='black', font=('微软雅黑', 12), padding=6)
        # 聊天记录标签
        self.chat_history.tag_config('user', foreground='#FFFFFF', background='#4A90E2',
                                     lmargin1=20, lmargin2=20, rmargin=20, spacing3=15)
        self.chat_history.tag_config('assistant', foreground='#333333', background='#F0F0F0',
                                     lmargin1=20, lmargin2=20, rmargin=60, spacing3=15)
        self.chat_history.tag_config('typing', foreground='#999999', font=('微软雅黑', 10))
        style.configure('Small.TButton', font=('微软雅黑',8), padding=2, relief='flat')

    def send_message(self, text=None):
        if self.is_responding:
            return
        user_text = text or self.user_input.get().strip()
        if not user_text:
            return
        # 清空输入
        self.user_input.delete(0, tk.END)
        self.set_input_state(False)
        self._append("You：", user_text, 'user')
        self.show_typing_indicator()
        threading.Thread(target=self._process_ai, args=(user_text,)).start()

    def _process_ai(self, user_text):
        # 更新历史
        self.conversation_history.append({"role":"user","content":user_text})
        try:
            resp = self.client.chat.completions.create(
                model="deepseek-chat", messages=self.conversation_history, stream=False)
            ai_text = resp.choices[0].message.content
            self.conversation_history.append({"role":"assistant","content":ai_text})
            self.master.after(0, lambda: self._handle_ai(ai_text))
        except Exception as e:
            err = f"错误：{str(e)}"
            self.master.after(0, lambda: self._append("Error：", err, 'assistant'))
        finally:
            self.master.after(0, lambda: self.set_input_state(True))

    def _handle_ai(self, text):
        self.hide_typing_indicator()
        start_idx = self.chat_history.index(tk.END)
        # 逐字动画
        self._animate(text, start_idx)
        # TTS
        message_id = str(datetime.now().timestamp())
        threading.Thread(target=self._text_to_speech, args=(text, message_id)).start()

    def _append(self, sender, msg, tag):
        self.chat_history.config(state='normal')
        self.chat_history.insert(tk.END, sender, tag)
        self.chat_history.insert(tk.END, msg + "\n\n", tag)
        self.chat_history.see(tk.END)
        self.chat_history.config(state='disabled')

    def show_typing_indicator(self):
        self.chat_history.config(state='normal')
        self.typing_tag = self.chat_history.insert(tk.END, "waiting...\n", 'typing')
        self.chat_history.see(tk.END)
        self.chat_history.config(state='disabled')

    def hide_typing_indicator(self):
        if self.typing_tag:
            self.chat_history.config(state='normal')
            self.chat_history.delete(self.typing_tag)
            self.typing_tag = None
            self.chat_history.config(state='disabled')

    def _animate(self, text, index, i=0):
        if i < len(text):
            self.chat_history.config(state='normal')
            self.chat_history.insert(f"{index}+{i}c", text[i], 'assistant')
            self.chat_history.config(state='disabled')
            self.chat_history.see(tk.END)
            self.master.after(30, lambda: self._animate(text, index, i+1))
        else:
            self.chat_history.config(state='normal')
            self.chat_history.insert(tk.END, "\n\n")
            self.chat_history.config(state='disabled')

    def set_input_state(self, enabled):
        state = 'normal' if enabled else 'disabled'
        self.user_input.config(state=state)
        self.send_btn.config(state=state)
        self.record_btn.config(state=state)
        self.is_responding = not enabled

    def toggle_recording(self):
        if not self.is_recording:
            self.is_recording = True
            self.record_btn.config(text="stop record")
            self.audio_frames = []
            self.p = pyaudio.PyAudio()
            self.stream = self.p.open(format=self.audio_format, channels=self.channels,
                                      rate=self.sample_rate, input=True,
                                      frames_per_buffer=self.chunk, stream_callback=self._audio_callback)
            self.stream.start_stream()
        else:
            self.is_recording = False
            self.record_btn.config(text="start record")
            self.stream.stop_stream(); self.stream.close(); self.p.terminate()
            fn = "temp_audio.wav"
            wf = wave.open(fn, 'wb'); wf.setnchannels(self.channels)
            wf.setsampwidth(self.p.get_sample_size(self.audio_format))
            wf.setframerate(self.sample_rate); wf.writeframes(b''.join(self.audio_frames)); wf.close()
            threading.Thread(target=self._transcribe, args=(fn,)).start()

    def _audio_callback(self, in_data, frame_count, time_info, status):
        self.audio_frames.append(in_data)
        return (in_data, pyaudio.paContinue)

    def _transcribe(self, filename):
        def cb(text): self.master.after(0, lambda: self.send_message(text))
        recognizer = SpeechRecognizer(
            app_id="04f8f3a5", api_key="c757f075f310cee1dce8af8e28d1ec4b",
            api_secret="Y2ZlMGMyZDJjYTcxMDEzYmY1N2M5MDJm")
        recognizer.transcribe(filename, cb)

    def _text_to_speech(self, text, filename):
        try:
            res = requests.post('http://127.0.0.1:9966/tts', data={
                "text": text, "prompt": "", "voice": "3333",
                "temperature": 0.3, "top_p": 0.7, "top_k": 20,
                "skip_refine": 0, "custom_voice": 0
            })
            if res.status_code == 200 and res.json().get('code') == 0:
                path = res.json()['filename'].replace('\\', '/')
                self.audio_files[filename] = path
                self.live2d_frame.wav.Start(path)
                threading.Thread(target=self.play_audio, args=(path,)).start()
        except Exception as e:
            print(f"TTS 失败: {e}")

    def play_audio(self, path):
        try:
            pygame.mixer.music.load(path); pygame.mixer.music.play()
        except Exception as e:
            print(f"播放失败: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    app = ChatApplication(root)
    root.protocol("WM_DELETE_WINDOW", lambda: messagebox.askokcancel("退出","确定要退出程序吗？") and root.destroy())
    root.mainloop()
