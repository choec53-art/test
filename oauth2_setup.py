"""
Gmail OAuth2 토큰 발급 스크립트
"""

import json
import os
import hashlib
import base64
import secrets
from urllib.parse import urlparse, parse_qs

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
REDIRECT_URI = "http://localhost:8090"
SCOPES = "https://mail.google.com/"
TOKEN_URI = "https://oauth2.googleapis.com/token"
TOKEN_FILE = "token.json"


def main():
    # PKCE code verifier/challenge 생성
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    auth_url = (
        f"https://accounts.google.com/o/oauth2/auth"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={SCOPES}"
        f"&access_type=offline"
        f"&prompt=consent"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )

    print(f"\n아래 URL을 브라우저에서 열어주세요:\n\n{auth_url}\n")
    redirect_url = input("인증 후 주소창의 URL 전체를 붙여넣으세요: ").strip()

    # URL에서 code 추출
    parsed = urlparse(redirect_url)
    code = parse_qs(parsed.query)["code"][0]

    # 토큰 교환
    resp = requests.post(TOKEN_URI, data={
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    })

    if resp.status_code != 200:
        print(f"[오류] 토큰 교환 실패: {resp.text}")
        return

    token_data = resp.json()
    with open(TOKEN_FILE, "w") as f:
        json.dump({
            "token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "token_uri": TOKEN_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scopes": [SCOPES],
        }, f, indent=2)

    print(f"\n[완료] {TOKEN_FILE} 생성 성공!")


if __name__ == "__main__":
    main()
