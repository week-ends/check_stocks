import requests
from bs4 import BeautifulSoup

import time
import random
import os
import json
import logging
import sys

import toml
from logging.handlers import RotatingFileHandler
from logging import FileHandler, Formatter, StreamHandler


class JSONFormatter(Formatter):
    def format(self, record):
        log_record = {
            "time": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        return json.dumps(log_record, ensure_ascii=False)


CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.toml")
config = toml.load(CONFIG_FILE)
TARGET_URL = config["target_url"]
WATCHING_TXT = config["watching_text"]
EXPECTED_TXT = config["expected_text"]
PROXIES_LIST = config.get("proxies", [])
TELEGRAM_TOKEN = config["telegram_bot_token"]
TELEGRAM_CHAT_ID = config["telegram_chat_id"]


# 요청 사이 랜덤 지연 범위 (초)
MIN_DELAY = config["min_delay"]
MAX_DELAY = config["max_delay"]

# 로그 파일 경로
LOG_PATH = "logs/check_button.log"
LOG_MAX_BYTES = 10**6
LOG_BACKUP_COUNT = 5
# 로그 디렉토리 생성
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
# 로거 설정
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
fh = FileHandler(LOG_PATH)
fh.setLevel(logging.DEBUG)
logger.addHandler(fh)

handler = RotatingFileHandler(
    LOG_PATH, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
)
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)


# Console output handler
console_handler = StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(JSONFormatter())
logger.addHandler(console_handler)


def send_telegram_message(text: str) -> None:
    """Telegram 봇으로 메시지를 전송"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram 환경변수가 설정되지 않음. 알림 전송 취소.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        resp = requests.post(url, json=payload)
        resp.raise_for_status()
        logger.info(f"Telegram 알림 전송 : {text}")
    except Exception as e:
        logger.error(f"Telegram 알림 전송 실패 : {e}")


# 사용자 에이전트 문자열 목록
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
]


class ItemInfoResponse:
    def __init__(self, title: str, price: str, is_contact_agent: bool):
        self.title = title
        self.price = price
        self.is_contact_agent = is_contact_agent

    def to_dict(self):
        return {
            "title": self.title,
            "price": self.price,
            "is_contact_agent": self.is_contact_agent,
        }

    @property
    def is_available_item(self) -> bool:
        """
        주어진 URL의 Cartier 품목이 구매 가능한지 확인하는 함수
        """
        return self.is_contact_agent is False


def scrape_cartier_watch(url: str) -> ItemInfoResponse:
    """
    Cartier 시계 상세 페이지에서 제품명, 가격, 이미지 URL 목록을 추출하는 함수
    """
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "max-age=0",
        "Priority": "u=0,i",
        "Sec-CH-UA": '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"macOS"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Upgrade-Insecure-Requests": "1",
    }

    # 시간 측정 시작
    start_time = time.perf_counter()
    # 인위적 지연
    delay_time = random.uniform(MIN_DELAY, MAX_DELAY)
    time.sleep(delay_time)
    # 요청 헤더 랜덤화
    headers["User-Agent"] = random.choice(USER_AGENTS)
    # 실제 스크래핑 수행 시간 측정
    req_start = time.perf_counter()
    # 백오프 및 타임아웃 설정
    MAX_RETRIES = config.get("max_retries", 3)
    BACKOFF_FACTOR = config.get("backoff_factor", 2)
    TIMEOUT = config.get("timeout", 3)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                f"요청 시도 {attempt}/{MAX_RETRIES}: url={url}, timeout={TIMEOUT}, headers={json.dumps(headers, ensure_ascii=False)}"
            )
            response = requests.get(url, headers=headers, timeout=TIMEOUT)
            break
        except requests.exceptions.RequestException as e:
            logger.warning(
                f"요청 실패 (시도 {attempt}/{MAX_RETRIES}): {e}\n"
                f"요청 파라미터: url={url}, timeout={TIMEOUT}\n"
                f"요청 헤더: {json.dumps(headers, ensure_ascii=False)}"
            )
            if attempt == MAX_RETRIES:
                raise
            sleep_time = BACKOFF_FACTOR ** (attempt - 1)
            logger.info(f"{sleep_time}초 후 재시도...")
            time.sleep(sleep_time)
    req_end = time.perf_counter()
    scrape_time = req_end - req_start

    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # 상품명 추출 (data-product-component="name" 사용)
    title_tag = soup.select_one('h1[data-product-component="name"]')
    title = title_tag.get_text(strip=True) if title_tag else None

    # 가격 추출 (data-product-component="price" 내 value 클래스 이용)
    price_tag = soup.select_one('div[data-product-component="price"] span.value')
    price = price_tag.get_text(strip=True) if price_tag else None

    # 상담원 연결 버튼 텍스트 확인
    availability_tag = soup.select_one(
        'a[data-product-component="availability-status"]'
    )
    availability_text = (
        availability_tag.get_text(strip=True) if availability_tag else None
    )
    is_contact_agent = availability_text == "상담원 연결"

    # 전체 수행 시간 계산
    total_time = time.perf_counter() - start_time
    # 로그 데이터 작성
    log_data = {
        "url": url,
        "headers": {
            "User-Agent": headers.get("User-Agent"),
            "Sec-CH-UA": headers.get("Sec-CH-UA"),
        },
        "delay_time": delay_time,
        "scrape_time": scrape_time,
        "total_time": total_time,
        "result": {
            "title": title,
            "price": price,
            "is_contact_agent": is_contact_agent,
        },
    }
    logger.debug(json.dumps(log_data, ensure_ascii=False))

    return ItemInfoResponse(title, price, is_contact_agent)


if __name__ == "__main__":
    logger.info("Cartier 시계 구매 가능 상태 확인 시작")

    # 대상 URL에서 정보 스크래핑
    url = TARGET_URL
    try:
        info: ItemInfoResponse = scrape_cartier_watch(url)
        # 구매 가능 상태 확인 후 Telegram 알림
        if info.is_available_item:
            message = (
                f"상품명: {info.title}\n"
                f"가격: {info.price}\n"
                "구매 가능 상태: 구매 가능\n"
                f"URL: {url}"
            )
            logger.info("구매 가능 상태 확인, Telegram 알림 전송")
            send_telegram_message(message)
        else:
            logger.info("아직 구매 불가 상태입니다.")
    except Exception as e:
        err_msg = f"스크래핑 오류 발생: {e}"
        logger.error(err_msg, exc_info=True)
        send_telegram_message(err_msg)
        sys.exit(1)
    else:
        logger.info("Cartier 시계 정보 스크래핑 완료")
    finally:
        logger.info("프로그램 종료")
        sys.exit(0)
