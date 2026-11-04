# 이음 공여자 앱 (React Native · Expo)

웹 화면과 같은 서버(FastAPI)를 쓰는 휴대폰 앱입니다. 로그인한 공여자 본인의 기록만 보입니다.

| 화면 | 하는 일 |
|---|---|
| 로그인 | 서버 주소 · 아이디 · 비밀번호. 토큰은 휴대폰 보안 저장소(SecureStore)에 보관 |
| 홈 | 오늘 기록 여부, 다음 추적검사, 혈압 판정, 검사 결과(쉬운 이름 + 기준 판정), 크레아티닌 변화 |
| 오늘 기록 | 측정 방법 안내, 큰 숫자 입력, 저장 직후 혈압 판정(정상 · 높음 · 매우 높음 · 낮음) |
| 이음 안내 | 추천 질문 + 채팅 (서버의 안내 Agent) |
| 설정 | **매일 같은 시각 소리 알림** (휴대폰 안에서 예약 → 인터넷 · 비용 없음) |

## 1. 처음 한 번: 프로젝트 만들기 (PowerShell)

Node.js(LTS)가 필요합니다. `donor-care` 폴더에서:

```powershell
npx create-expo-app@latest mobile --template blank
cd mobile
npx expo install expo-secure-store expo-notifications expo-device
Copy-Item ..\mobile_src\App.js . -Force
Copy-Item ..\mobile_src\src . -Recurse -Force
```

## 2. 서버를 휴대폰에서 보이게 켜기

휴대폰과 PC가 **같은 와이파이**여야 합니다. 서버를 켤 때 `--host 0.0.0.0` 을 붙입니다.

```powershell
uvicorn main:app --reload --host 0.0.0.0
ipconfig      # "IPv4 주소" 가 PC 주소 (예: 192.168.0.10)
```

Windows 방화벽 창이 뜨면 "개인 네트워크 허용"을 누릅니다.

## 3. 앱 실행

```powershell
cd mobile
npx expo start
```

휴대폰에 **Expo Go** 앱을 설치하고 화면의 QR 코드를 찍습니다.
앱 로그인 화면의 서버 주소에 `http://PC주소:8000` 을 넣고, 웹에서 가입한 공여자 아이디로 로그인합니다.

## 4. (선택) 블루투스 혈압계 · 체중계

표준 블루투스 규격(혈압 0x1810, 체중 0x181D)을 지원하는 기기만 연결됩니다.
블루투스 모듈은 Expo Go 에서 쓸 수 없어서 **개발 빌드**가 필요합니다 (안드로이드: Android Studio 설치).

```powershell
npx expo install react-native-ble-plx
```

`app.json` 의 `"expo"` 안에 추가:

```json
"plugins": [["react-native-ble-plx", { "isBackgroundEnabled": false,
  "bluetoothAlwaysPermission": "혈압계와 체중계에서 측정값을 받아 오기 위해 블루투스를 사용합니다." }]]
```

`src/bleBridge.js` 의 마지막 줄을 `export { default } from "./ble";` 로 바꾼 뒤:

```powershell
npx expo run:android
```

기록 화면에 "혈압계에서 받기 / 체중계에서 받기" 버튼이 생깁니다. 받은 값은 바로 저장하지 않고 숫자 칸에 채워서, 본인이 확인한 뒤 저장합니다.

## 파일

| 파일 | 내용 |
|---|---|
| `App.js` | 화면 전체 (로그인 · 홈 · 기록 · 채팅 · 설정) |
| `src/api.js` | 서버 요청, 토큰 보관 (`Authorization: Bearer`) |
| `src/reminders.js` | 매일 알림 예약 (안드로이드 알림 채널: 소리 + 진동) |
| `src/bleParse.js` | 블루투스 측정값(IEEE 11073 SFLOAT) → 숫자 |
| `src/ble.js` | 기기 찾기 · 연결 · 측정값 받기 (react-native-ble-plx) |
| `src/bleBridge.js` | 블루투스 기능 켜고 끄는 스위치 |
