import os
import requests
from openai import OpenAI
from google import genai
from typing import List, Dict, Any

class LLMManager:
    def __init__(self):
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        self.ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")

        # Initialize clients
        self.openai_client = None
        if self.openai_api_key:
            try:
                self.openai_client = OpenAI(api_key=self.openai_api_key)
            except Exception as e:
                print(f"Failed to initialize OpenAI client: {e}")

        self.gemini_client = None
        if self.gemini_api_key:
            try:
                self.gemini_client = genai.Client(api_key=self.gemini_api_key)
            except Exception as e:
                print(f"Failed to configure Gemini: {e}")

    def get_available_providers_and_models(self) -> Dict[str, List[str]]:
        """回傳目前可用的 providers 與其對應的 models"""
        providers = {}

        # 1. Ollama (Always check if available locally)
        try:
            resp = requests.get(f"{self.ollama_host}/api/tags", timeout=2)
            if resp.status_code == 200:
                data = resp.json()
                models = [model['name'] for model in data.get('models', [])]
                if models:
                    providers['Ollama'] = models
        except Exception:
            pass  # Ollama not running or reachable

        # 2. OpenAI
        if self.openai_client:
            providers['OpenAI'] = ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"]
            # To fetch dynamically, could use self.openai_client.models.list() but usually it's better to offer common ones.

        # 3. Gemini
        if self.gemini_client:
            # google-genai doesn't easily return a clean list of just text models immediately without filtering thoroughly, 
            # so providing the common ones is better, or we can use standard known names
            providers['Gemini'] = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"]

        return providers

    def build_system_prompt(self, action: str) -> str:
        """建立共用的 System Prompt (針對 8B 模型優化)"""
        if action == "polish":
            return (
                "你是一位專業且嚴謹的字幕編輯。請幫我「潤飾」以下傳入的字幕句子，"
                "修正聽打錯字、改善流暢度，並補上適當的標點符號。\n\n"
                "【嚴格執行以下規則】\n"
                "1. 絕對保持原意：只修正語病，不可無中生有、不可過度腦補、不可改變講者原本的語氣與對話關係。\n"
                "2. 鎖定專有名詞：遇到英文縮寫或專業術語（如 VSCC, TNCAP, Euro NCAP, ARTC 等），務必保留原文，絕對不可自行替換或擴充解釋。\n"
                "3. 零解釋輸出：你只能輸出潤飾後的最終句子。絕對禁止包含任何括號說明（例如絕對不可輸出「無需潤飾，直接回傳原句」或「若需更簡潔版」等文字）。\n"
                "4. 禁用排版符號：絕對不要使用 Markdown 語法（如粗體 **）來強調任何字詞。\n\n"
                "若判斷該句不需要潤飾，請直接原封不動回傳原句，不要加上任何廢話或對話引號。"
            )
        elif action == "translate":
            return (
                "你是一位專業的翻譯專家。請將以下傳入的字幕句子翻譯成「繁體中文」，確保語氣自然且適合做為影片字幕。\n\n"
                "【嚴格執行以下規則】\n"
                "1. 保留專有名詞：遇到車輛、科技或安全相關的英文縮寫（如 VSCC, NCAP 等），請保持原樣，不需硬翻成中文。\n"
                "2. 零解釋輸出：你只能輸出翻譯後的最終句子。絕對禁止包含任何括號說明、問候語、解析步驟或對話引號。\n"
                "3. 不要自行補充原文沒有的資訊或主詞。"
            )
        else:
            return "請處理以下文字。"

    def process_sentence(self, text: str, provider: str, model: str, action: str, custom_prompt: str = "",
                         prev_text: str = "", next_text: str = "", temperature: float = 0.3, max_tokens: int = 1024, thinking_level: str = "medium") -> str:
        """處理單一句句話 (潤飾或翻譯)，支援上下文與自訂參數"""
        if not text.strip():
            return text

        system_prompt = custom_prompt.strip() if custom_prompt and custom_prompt.strip() else self.build_system_prompt(action)

        # 組合包含上下文的 Prompt
        context_msg = ""
        if prev_text or next_text:
            context_msg += "【上下文參考】\n"
            if prev_text:
                context_msg += f"前一句: {prev_text}\n"
            if next_text:
                context_msg += f"後一句: {next_text}\n"
            context_msg += "\n"
        
        user_message = f"{context_msg}【請處理以下當前句子】\n{text}"

        if provider == "Ollama":
            return self._process_ollama(user_message, model, system_prompt, temperature, max_tokens, thinking_level)
        elif provider == "OpenAI":
            return self._process_openai(user_message, model, system_prompt, temperature, max_tokens, thinking_level)
        elif provider == "Gemini":
            return self._process_gemini(user_message, model, system_prompt, temperature, max_tokens)
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")

    def _process_ollama(self, text: str, model: str, system_prompt: str, temperature: float, max_tokens: int, thinking_level: str) -> str:
        url = f"{self.ollama_host}/api/chat"
        # 針對支援思考等級的模型，有些可能需要額外參數，先保留 num_predict 和 temperature
        options = {
            "temperature": temperature,
            "num_predict": max_tokens
        }
        # 如果模型支援，可在此處轉換 thinking_level 對應的額外參數
        
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            "options": options,
            "stream": False
        }
        try:
            resp = requests.post(url, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            return data.get('message', {}).get('content', '').strip()
        except requests.exceptions.RequestException as e:
            print(f"Ollama API Error: {e}")
            return text

    def _process_openai(self, text: str, model: str, system_prompt: str, temperature: float, max_tokens: int, thinking_level: str) -> str:
        if not self.openai_client:
            raise ValueError("OpenAI client not initialized.")
        
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
        }
        
        # 針對 o1 / o3 系列模型處理 (不支援 temperature 且 max_tokens 參數不同)
        if model.startswith("o1") or model.startswith("o3"):
            kwargs["max_completion_tokens"] = max_tokens
            kwargs["reasoning_effort"] = thinking_level
            # o1 模型通常沒有 system role，需轉換為 user, 或保留依 API 版本而定
            # 最新 API 已支援 o1 的 system/developer role
        else:
            kwargs["temperature"] = temperature
            kwargs["max_tokens"] = max_tokens
            
        try:
            response = self.openai_client.chat.completions.create(**kwargs)
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"OpenAI API Error: {e}")
            return text

    def _process_gemini(self, text: str, model: str, system_prompt: str, temperature: float, max_tokens: int) -> str:
        if not self.gemini_client:
            raise ValueError("Gemini client is not configured.")
        try:
            response = self.gemini_client.models.generate_content(
                model=model,
                contents=text,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            )
            return response.text.strip()
        except Exception as e:
            print(f"Gemini API Error: {e}")
            return text

llm_manager = LLMManager()
