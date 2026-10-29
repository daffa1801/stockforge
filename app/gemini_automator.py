import base64
import os
import re
import shutil
import time
from typing import Optional, Dict, Any, List
from playwright.sync_api import sync_playwright, Playwright, BrowserContext, Page, TimeoutError

try:
    from app.queue_manager import is_file_valid
except ImportError:
    from queue_manager import is_file_valid

IMAGE_SELECTORS = (
    'img[src*="googleusercontent.com"], img[src*="blob:"], '
    'div.image-container img, generated-image img, button.image-button img'
)

DOWNLOAD_BUTTON_SELECTORS = (
    'button[aria-label*="ukuran penuh" i], '
    'button[aria-label*="full size" i], '
    'button[aria-label*="Download gambar" i], '
    'button[aria-label*="Unduh gambar" i]'
)

MODEL_RESPONSE_SELECTORS = (
    'model-response, message-content, div.model-response-text, '
    'div.message-content, response-container, div.model-turn, '
    'mat-snack-bar-container, div[role="alert"], div[role="status"], '
    'div.response-container, div.markdown'
)

RATE_LIMIT_PHRASES = [
    # English specific
    "reached your limit",
    "reached your daily limit",
    "reached the daily limit",
    "reached a daily limit",
    "reached the limit",
    "reached your prompt limit",
    "image generation limit",
    "daily limit for image generation",
    "daily limit for generating images",
    "daily limit for creating images",
    "limit for image generation",
    "limit for generating images",
    "limit for creating images",
    "can't generate any more images",
    "cannot generate any more images",
    "unable to generate more images",
    "unable to create more images",
    "quota exceeded",
    "quota has been reached",
    "daily quota",
    "out of image generation",
    "run out of image",
    "hit your daily limit",
    "hit your limit",
    "try again tomorrow",
    "come back tomorrow",
    "too many requests",
    "try again later",
    "temporarily unavailable",
    "upgrade to gemini advanced",
    "switch to gemini advanced",

    # Indonesian specific
    "mencapai batas",
    "telah mencapai batas",
    "sudah mencapai batas",
    "mencapai batas harian",
    "batas pembuatan gambar",
    "batas membuat gambar",
    "batas harian pembuatan gambar",
    "batas harian untuk membuat gambar",
    "batas harian anda",
    "batas harian telah tercapai",
    "kuota pembuatan gambar",
    "kuota gambar",
    "kuota harian",
    "kuota anda habis",
    "kuota anda telah habis",
    "kuota telah habis",
    "kuota habis",
    "tidak dapat membuat gambar lagi",
    "tidak bisa membuat gambar lagi",
    "coba lagi besok",
    "kembali lagi besok",
    "coba lagi nanti",
    "tingkatkan ke gemini advanced",
    "beralih ke gemini advanced",
]

def is_rate_limit_text(text: str) -> bool:
    """Checks whether a string contains an actual rate limit indicator from model replies or alerts."""
    if not text:
        return False
    lowered = text.lower()

    # 1. Direct phrase match
    if any(phrase in lowered for phrase in RATE_LIMIT_PHRASES):
        return True

    # 2. Smart word combination for conversational model variations
    has_limit_keyword = any(w in lowered for w in [
        "limit", "batas", "kuota", "quota"
    ])
    has_image_keyword = any(w in lowered for w in [
        "image", "gambar", "foto", "render", "generation", "pembuatan", "membuat"
    ])
    has_exhausted_keyword = any(w in lowered for w in [
        "reached", "exceeded", "habis", "tercapai", "tomorrow", "besok",
        "can't", "cannot", "tidak dapat", "tidak bisa", "run out", "hit", "advanced"
    ])

    if has_limit_keyword and (has_image_keyword or "daily" in lowered or "harian" in lowered) and has_exhausted_keyword:
        return True

    return False

def format_image_prompt(prompt: str) -> str:
    """Ensures prompt explicitly asks Gemini for image generation."""
    clean = prompt.strip()
    lowered = clean.lower()
    if lowered.startswith("generate an image") or lowered.startswith("create an image") or lowered.startswith("draw an image"):
        return clean
    if lowered.startswith("generate image") or lowered.startswith("create image"):
        return clean
    return f"Generate an image: {clean}"

def save_base64_data_url(data_url: str, output_path: str) -> bool:
    """Decodes data URL (data:image/...;base64,...) and writes to output_path."""
    try:
        match = re.match(r"^data:image/[^;]+;base64,(.+)$", data_url, re.DOTALL)
        if not match:
            return False
        b64_data = match.group(1)
        raw_bytes = base64.b64decode(b64_data)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(raw_bytes)
        return True
    except Exception as e:
        print(f"[ERROR] Failed to save base64 data: {e}")
        return False

def normalize_chrome_profiles(profiles: Any, base_dir: str, default_profile: Optional[str] = None) -> List[str]:
    """Normalizes a list or string of Chrome profile directories into absolute paths."""
    raw_list: List[str] = []
    if isinstance(profiles, str):
        for line in profiles.replace(",", "\n").splitlines():
            s = line.strip()
            if s:
                raw_list.append(s)
    elif isinstance(profiles, list):
        for item in profiles:
            s = str(item).strip()
            if s:
                raw_list.append(s)

    if not raw_list and default_profile:
        raw_list.append(default_profile)

    if not raw_list:
        raw_list.append("chrome_profile")

    result: List[str] = []
    for item in raw_list:
        abs_path = item if os.path.isabs(item) else os.path.normpath(os.path.join(base_dir, item))
        if abs_path not in result:
            result.append(abs_path)

    return result

def find_chrome_executable(configured_path: Optional[str] = None) -> Optional[str]:
    """Finds Chrome executable across standard Windows installation paths."""
    if configured_path and os.path.exists(configured_path):
        return configured_path
        
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return configured_path if configured_path else None

class GeminiAutomator:
    def __init__(
        self,
        user_data_dir: str,
        chrome_executable: Optional[str] = None,
        headless: bool = False,
        watchdog_timeout_sec: int = 1200,
    ):
        self.user_data_dir = os.path.abspath(user_data_dir)
        self.chrome_executable = find_chrome_executable(chrome_executable)
        self.headless = headless
        self.watchdog_timeout_sec = watchdog_timeout_sec
        
        self.playwright: Optional[Playwright] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.is_running = False

    def launch(self) -> Page:
        """Launches native Chrome with persistent user data context."""
        os.makedirs(self.user_data_dir, exist_ok=True)
        self.playwright = sync_playwright().start()
        
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--start-maximized",
            "--disable-features=DownloadBubble,DownloadBubbleV2",
            "--no-shell-integration",
        ]

        downloads_dir = os.path.join(self.user_data_dir, "downloads")
        os.makedirs(downloads_dir, exist_ok=True)
        
        launch_kwargs = {
            "user_data_dir": self.user_data_dir,
            "headless": self.headless,
            "args": args,
            "viewport": None,  # Match maximized window
            "accept_downloads": True,
            "downloads_path": downloads_dir,
        }
        
        if self.chrome_executable and os.path.exists(self.chrome_executable):
            launch_kwargs["executable_path"] = self.chrome_executable
        else:
            launch_kwargs["channel"] = "chrome"
            
        self.context = self.playwright.chromium.launch_persistent_context(**launch_kwargs)
        self.page = self.get_active_page()
        self.is_running = True
        return self.page

    def get_active_page(self) -> Page:
        """Dynamically retrieves the latest open and active tab in the browser context."""
        if not self.context:
            raise RuntimeError("Browser is not running.")
            
        open_pages = [p for p in self.context.pages if not p.is_closed()]
        if not open_pages:
            self.page = self.context.new_page()
            return self.page
            
        # If current page is valid and on Gemini, keep it
        if self.page and not self.page.is_closed():
            latest = open_pages[-1]
            if "gemini.google.com" in latest.url or "accounts.google.com" in latest.url:
                self.page = latest
                return self.page
            return self.page
            
        self.page = open_pages[-1]
        return self.page

    def navigate_to_gemini(self) -> None:
        """Navigates to Gemini web app."""
        page = self.get_active_page()
        page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=45000)
        time.sleep(3)

    def start_new_chat(self) -> None:
        """Starts a fresh chat session to prevent old messages/alerts from polluting context."""
        page = self.get_active_page()
        try:
            new_chat_btn = page.locator(
                'a[href*="/app"], button[aria-label*="New chat"], button:has-text("New chat"), button:has-text("Obrolan baru")'
            )
            if new_chat_btn.count() > 0 and new_chat_btn.first.is_visible():
                new_chat_btn.first.click()
                time.sleep(2)
                return
        except Exception:
            pass
            
        try:
            page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
        except Exception:
            pass

    def refresh_and_recover(self) -> None:
        """Refreshes the active Gemini tab and recovers context when generation is stuck."""
        page = self.get_active_page()
        print("\n[WATCHDOG] 20 menit / waktu batas tercapai tanpa hasil. Me-refresh tab Gemini...")
        try:
            page.reload(wait_until="domcontentloaded", timeout=45000)
            time.sleep(4)
        except Exception as e:
            print(f"[WATCHDOG] Gagal reload: {e}. Mengarahkan ulang ke halaman utama...")
            try:
                page.goto("https://gemini.google.com/app", wait_until="domcontentloaded", timeout=45000)
                time.sleep(3)
            except Exception:
                pass

    def is_logged_in(self) -> bool:
        """Checks if current page has an active logged-in Gemini session."""
        try:
            page = self.get_active_page()
        except Exception:
            return False
            
        current_url = page.url
        if "accounts.google.com" in current_url:
            return False
            
        # Check for chat input element
        chat_input = page.locator(
            'div[contenteditable="true"], rich-textarea, textarea, div.ql-editor'
        )
        if chat_input.count() > 0:
            return True
            
        # Check for sign in buttons
        sign_in_btn = page.locator(
            'a[href*="accounts.google.com"], button:has-text("Sign in"), button:has-text("Masuk")'
        )
        if sign_in_btn.count() > 0:
            return False
            
        return False

    def wait_for_user_login(self, timeout_sec: int = 300) -> bool:
        """Waits until user signs in to Google on the opened browser window."""
        print("[INFO] Menunggu kamu login Google di jendela Chrome...")
        start_time = time.time()
        while time.time() - start_time < timeout_sec:
            if self.is_logged_in():
                print("[INFO] Berhasil terdeteksi login ke Gemini!")
                return True
            time.sleep(3)
        return False

    def check_for_rate_limit(self, baseline_model_count: int = 0, baseline_alert_count: int = 0) -> Optional[str]:
        """Detects if Gemini displayed a quota / limit notice from model reply or alerts."""
        try:
            page = self.get_active_page()
        except Exception:
            return None

        # 1. Inspect model reply bubbles (balasan chat / respons teks Gemini)
        try:
            model_bubbles = page.locator(
                'model-response, message-content, div.model-response-text, '
                'div.message-content, response-container, div.model-turn, div.markdown'
            )
            count = model_bubbles.count()
            # Inspect any new model response bubbles added since baseline
            for i in range(max(0, baseline_model_count), count):
                try:
                    txt = model_bubbles.nth(i).inner_text().strip()
                    if is_rate_limit_text(txt):
                        return txt
                except Exception:
                    continue

            # Also check the latest model response bubble
            if count > 0:
                try:
                    latest_txt = model_bubbles.last.inner_text().strip()
                    if is_rate_limit_text(latest_txt):
                        return latest_txt
                except Exception:
                    pass
        except Exception:
            pass

        # 2. Inspect snackbars, alert banners, and error toasts
        try:
            alerts = page.locator(
                'mat-snack-bar-container, div[role="alert"], div[role="status"], [class*="snackbar"]'
            )
            count = alerts.count()
            for i in range(max(0, baseline_alert_count), count):
                try:
                    txt = alerts.nth(i).inner_text().strip()
                    if is_rate_limit_text(txt):
                        return txt
                except Exception:
                    continue
        except Exception:
            pass

        return None

    def _extract_and_save_image(self, page: Page, img_src: str, target_file: str) -> bool:
        """Attempts image download via data URL, in-browser fetch, and Playwright network request."""
        # 1. Direct Data URL
        if img_src.startswith("data:"):
            return save_base64_data_url(img_src, target_file)
            
        # 2. In-browser fetch
        try:
            fetch_script = """
            async (src) => {
                const response = await fetch(src, { credentials: 'include' });
                if (!response.ok) throw new Error('Fetch status ' + response.status);
                const blob = await response.blob();
                return new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onloadend = () => resolve(reader.result);
                    reader.onerror = reject;
                    reader.readAsDataURL(blob);
                });
            }
            """
            data_url = page.evaluate(fetch_script, img_src)
            if data_url and save_base64_data_url(data_url, target_file):
                if is_file_valid(target_file, min_size=10240):
                    return True
        except Exception:
            pass
            
        # 3. Playwright network request (bypasses browser CORS constraints)
        try:
            res = page.request.get(img_src, timeout=30000)
            if res.ok:
                body = res.body()
                os.makedirs(os.path.dirname(os.path.abspath(target_file)), exist_ok=True)
                with open(target_file, "wb") as f:
                    f.write(body)
                if is_file_valid(target_file, min_size=10240):
                    return True
        except Exception:
            pass
            
        return False

    def generate_image(self, prompt: str, target_file: str, timeout: Optional[int] = None, watchdog_retries: int = 1) -> Dict[str, Any]:
        """Submits prompt to Gemini, waits for image to render, and saves to target_file."""
        wait_limit = timeout if timeout is not None else self.watchdog_timeout_sec
        page = self.get_active_page()
        formatted_prompt = format_image_prompt(prompt)
        
        # Count existing download buttons, images, alerts, and model responses before sending
        baseline_dl_buttons = page.locator(DOWNLOAD_BUTTON_SELECTORS).count()
        baseline_images = page.locator(IMAGE_SELECTORS).count()
        baseline_alerts = page.locator(
            'mat-snack-bar-container, div[role="alert"], div[role="status"], [class*="snackbar"]'
        ).count()
        baseline_model_responses = page.locator(
            'model-response, message-content, div.model-response-text, div.message-content, response-container, div.model-turn'
        ).count()
        
        # Locate input box
        input_selector = 'div[contenteditable="true"], rich-textarea, textarea, div.ql-editor'
        try:
            page.wait_for_selector(input_selector, state="visible", timeout=15000)
        except TimeoutError:
            if not self.is_logged_in():
                return {"status": "login_required", "message": "Sesi login tidak ditemukan."}
            return {"status": "error", "message": "Kotak input prompt Gemini tidak ditemukan."}
            
        input_el = page.locator(input_selector).first
        input_el.click()
        time.sleep(0.5)
        
        # Fill prompt cleanly
        try:
            page.keyboard.insert_text(formatted_prompt)
        except Exception:
            input_el.fill(formatted_prompt)
            
        time.sleep(1)
        
        # Send prompt
        send_btn = page.locator(
            'button[aria-label*="Send message"], button[aria-label*="Kirim pesan"], button.send-button, mat-icon[data-mat-icon-name="send"]'
        )
        if send_btn.count() > 0 and send_btn.first.is_visible():
            send_btn.first.click()
        else:
            page.keyboard.press("Enter")
            
        print(f"[STATUS] Prompt terkirim. Menunggu Gemini memproses gambar...")
        
        # Wait for new download button or new image card to appear
        start_wait = time.time()
        image_ready = False
        
        while time.time() - start_wait < wait_limit:
            # Check for new rate limit notice from model reply or alerts
            limit_msg = self.check_for_rate_limit(
                baseline_model_count=baseline_model_responses,
                baseline_alert_count=baseline_alerts
            )
            if limit_msg:
                print(f"[LIMIT TERDETEKSI] Batas kuota ditemukan dari balasan Gemini: {limit_msg}")
                return {"status": "rate_limit", "message": limit_msg}

            dl_buttons = page.locator(DOWNLOAD_BUTTON_SELECTORS)
            if dl_buttons.count() > baseline_dl_buttons:
                image_ready = True
                break
                
            # Check if image card or blob image has appeared
            image_cards = page.locator('button.image-button, [class*="image-button"], img[src*="blob:"]')
            if image_cards.count() > baseline_images:
                try:
                    image_cards.last.scroll_into_view_if_needed(timeout=2000)
                    image_cards.last.hover(timeout=2000)
                except Exception:
                    pass
                time.sleep(1)
                if page.locator(DOWNLOAD_BUTTON_SELECTORS).count() > baseline_dl_buttons:
                    image_ready = True
                    break
                    
            time.sleep(2)
            
        if not image_ready:
            if watchdog_retries > 0:
                print(f"[WATCHDOG] Generation tidak selesai dalam {wait_limit}s. Me-refresh tab dan mencoba ulang...")
                self.refresh_and_recover()
                return self.generate_image(prompt, target_file, timeout=wait_limit, watchdog_retries=watchdog_retries - 1)
            return {"status": "timeout", "message": f"Timeout {wait_limit}s menunggu hasil render gambar dari Gemini."}
            
        print(f"[STATUS] Gambar selesai dibuat! Mengunduh gambar...")
        
        # Try native high-resolution download button
        dl_buttons = page.locator(DOWNLOAD_BUTTON_SELECTORS)
        if dl_buttons.count() > 0:
            target_btn = dl_buttons.last
            try:
                img_card = page.locator('button.image-button, [class*="image-button"], img[src*="blob:"]').last
                if img_card.count() > 0:
                    img_card.scroll_into_view_if_needed(timeout=2000)
                    img_card.hover(timeout=2000)
                    time.sleep(0.5)
            except Exception:
                pass
                
            try:
                with page.expect_download(timeout=35000) as dl_info:
                    target_btn.click(force=True)
                download = dl_info.value

                # Safe download handling: read finished temp download path and copy cleanly
                # to prevent Windows Explorer "temp missing / item not found" popups.
                dl_temp = download.path()
                if dl_temp and os.path.exists(dl_temp):
                    if is_file_valid(dl_temp, min_size=10240):
                        os.makedirs(os.path.dirname(os.path.abspath(target_file)), exist_ok=True)
                        shutil.copy2(dl_temp, target_file)
                        if is_file_valid(target_file, min_size=10240):
                            file_size = os.path.getsize(target_file)
                            return {
                                "status": "success",
                                "file": target_file,
                                "bytes": file_size,
                            }
                else:
                    download.save_as(target_file)
                    if is_file_valid(target_file, min_size=10240):
                        file_size = os.path.getsize(target_file)
                        return {
                            "status": "success",
                            "file": target_file,
                            "bytes": file_size,
                        }
            except Exception as e:
                print(f"[WARN] Tombol unduh timeout ({e}). Mencoba fallback ekstraksi canvas...")
                
        # Fallback: Canvas extraction
        try:
            canvas_script = """
            () => {
                const img = document.querySelector('button.image-button:last-of-type img') || 
                            document.querySelector('button.image-button img') || 
                            document.querySelector('img[src*="blob:"]:last-of-type') ||
                            document.querySelector('img[src*="blob:"]');
                if (!img) return null;
                const canvas = document.createElement('canvas');
                canvas.width = img.naturalWidth || 1024;
                canvas.height = img.naturalHeight || 1024;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0);
                return canvas.toDataURL('image/jpeg', 0.98);
            }
            """
            data_url = page.evaluate(canvas_script)
            if data_url and save_base64_data_url(data_url, target_file):
                if is_file_valid(target_file, min_size=10240):
                    file_size = os.path.getsize(target_file)
                    return {
                        "status": "success",
                        "file": target_file,
                        "bytes": file_size,
                    }
        except Exception:
            pass
            
        # Clean up corrupted file if any (only if invalid / zero-sized)
        if os.path.exists(target_file) and not is_file_valid(target_file, min_size=10240):
            try:
                os.remove(target_file)
            except Exception:
                pass
                
        return {"status": "error", "message": "Gagal menyimpan atau memverifikasi file gambar hasil download."}

    def close(self) -> None:
        """Closes browser context and playwright instance."""
        try:
            if self.context:
                self.context.close()
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass
        finally:
            self.is_running = False

    def switch_profile(self, new_user_data_dir: str) -> Page:
        """Closes the current browser context and launches a new one with a different profile."""
        print(f"[ROTASI] Menutup browser profil aktif dan beralih ke: {new_user_data_dir}")
        self.close()
        time.sleep(2)
        self.user_data_dir = os.path.abspath(new_user_data_dir)
        return self.launch()
