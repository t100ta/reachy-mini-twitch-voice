# Reachy Mini 実機接続チェックリスト（SSH + Python）

このドキュメントは、Reachy Mini の Raspberry Pi に SSH で入り、
Python で SDK と発話 API を確認するための手順です。

コマンド例の `<PROJECT_DIR_ON_REACHY>` / `<REACHY_USER>` / `<REACHY_HOST>` は実環境の値に置き換えてください。

## 0. 前提

- Reachy Mini と操作PCが同じネットワークにいること
- Reachy Mini の SSH ユーザー名/パスワードがわかること
- 操作PCで `ssh` コマンドが使えること

## 1. Reachy Mini のIPを確認

### 方法A: mDNS 名で確認

```bash
ping reachy-mini.local
```

- 応答があれば、`reachy-mini.local` をそのまま使えます。

### 方法B: ルーター管理画面で確認

- 接続デバイス一覧から Reachy Mini を探してIPを控える
- 例: `192.168.1.120`

## 2. SSHでログイン

```bash
ssh <USER>@<REACHY_HOST>
```

例:

```bash
ssh pi@192.168.1.120
```

- 初回接続では `yes` を入力
- パスワード入力は画面表示されない（正常）

## 3. Pythonの確認

```bash
python3 --version
```

## 4. SDK import の確認

```bash
python3 -c "import reachy_mini; print('ok')"
```

- `ok` が出れば import 成功
- 失敗したらエラーメッセージを保存

## 5. Twitch トークン取得（公式 Device Flow）

1. Device code 発行

```bash
curl -s -X POST 'https://id.twitch.tv/oauth2/device' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "client_id=YOUR_CLIENT_ID&scopes=chat:read" | tee /tmp/twitch_device.json
```

2. `/tmp/twitch_device.json` の `verification_uri_complete` をブラウザで開いて認可

3. Token 交換

```bash
curl -s -X POST 'https://id.twitch.tv/oauth2/token' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "client_id=YOUR_CLIENT_ID&device_code=DEVICE_CODE&grant_type=urn:ietf:params:oauth:grant-type:device_code"
```

4. 返却JSONの `access_token` を `.env.local` に設定

```env
TWITCH_OAUTH_TOKEN=oauth:ACCESS_TOKEN
```

5. validate で有効性確認（`oauth:` プレフィックスは付けない）

```bash
TOKEN="ACCESS_TOKEN"
curl -s -H "Authorization: OAuth ${TOKEN}" https://id.twitch.tv/oauth2/validate
```

6. `invalid access token` が出る場合
- トークン失効です。手順1-4を再実行して新しい token に差し替え
- `TWITCH_NICK` は token を発行した Twitch ユーザー名と一致させる

7. OpenAI設定（会話生成）

```env
OPENAI_API_KEY=...
OPENAI_REALTIME_MODEL=gpt-4o-mini
CONVERSATION_INPUT_MODE=twitch
TWITCH_MESSAGE_CONTEXT_WINDOW=30
```

## 6. 実機の発話API候補を調べる

```bash
python3 - << 'PY'
from reachy_mini import ReachyMini
r = ReachyMini(connection_mode="auto")
print("type:", type(r))
print("attrs sample:", [x for x in dir(r) if any(k in x.lower() for k in [
    "audio","sound","speak","say","voice","tts","play","media"
])])
if hasattr(r, "client"):
    c = r.client
    print("client type:", type(c))
    print("client attrs sample:", [x for x in dir(c) if any(k in x.lower() for k in [
        "audio","sound","speak","say","voice","tts","play","media"
    ])])
PY
```

この出力で、`media.play_sound` 経由で扱うべきかを確定します。

## 7. 最小発話テスト

```bash
python3 - << 'PY'
from reachy_mini import ReachyMini
r = ReachyMini(connection_mode="auto")
print("media:", hasattr(r, "media"), "play_sound:", hasattr(getattr(r, "media", None), "play_sound"))
PY
```

続けて `play_sound` の最小テスト:

```bash
python3 - << 'PY'
import wave, math, struct, tempfile
from reachy_mini import ReachyMini

fd = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
path = fd.name
fd.close()

sr = 16000
with wave.open(path, "w") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    for i in range(int(sr * 0.5)):
        v = int(12000 * math.sin(2 * math.pi * 440 * i / sr))
        w.writeframes(struct.pack("<h", v))

r = ReachyMini(connection_mode="auto")
r.media.play_sound(path)
print("played:", path)
PY
```

## 8. 必要な情報（この後の実装確定に使う）

以下をメモして共有してください。

1. `python3 --version` の結果
2. 手順6の全出力
3. 手順7の成功/失敗
4. 接続先ホスト（IP or `.local`）
5. 実行場所（Reachy本体上 / 別PC）

## 9. TTS設定固定（本番前に必須）

`espeak-ng` + `media.play_sound` を固定で使います。

```bash
export REACHY_TTS_ENGINE=espeak-ng
export REACHY_TTS_LANG=ja
export REACHY_MOTION_STYLE=official
export REACHY_IDLE_STYLE=attentive
export REACHY_IDLE_FIRST_DELAY_SEC=3.0
export REACHY_IDLE_GLANCE_INTERVAL_SEC=10.0
export REACHY_SPEECH_MOTION_SCALE=0.65
export REACHY_EMOTION_MOTION_ENABLED=true
```

補足:
- `backend_status.ready` は 1.5.0 でも初期化直後は false のことがあります。`/api/state/full` が 200 なら実質稼働です。
- `REACHY_MOTION_STYLE=official` では待機が `attentive -> breathing -> low-frequency phrase` の順に遷移します。
- 実機チューニング前は上記の既定値のまま始め、違和感があれば `REACHY_IDLE_GLANCE_INTERVAL_SEC` と `REACHY_SPEECH_MOTION_SCALE` を先に調整します。

## 10. 終了

```bash
exit
```

## 11. 再同期/再インストール時の安全運用

`.env.local` が上書きされると認証エラーになりやすいため、プロジェクト外に配置して固定参照を推奨します。

```bash
mkdir -p ~/.config/reachy-mini-twitch-voice
cp <PROJECT_DIR_ON_REACHY>/.env.local.example ~/.config/reachy-mini-twitch-voice/.env.local
chmod 600 ~/.config/reachy-mini-twitch-voice/.env.local
```

実行は毎回以下で統一:

```bash
cd <PROJECT_DIR_ON_REACHY>
PYTHONPATH=src python3 -m reachy_twitch_voice.main \
  --env-file ~/.config/reachy-mini-twitch-voice/.env.local \
  --log-level INFO
```

PCから同期するときは `.env.local` を除外:

```bash
rsync -av --delete \
  --exclude '.env.local' \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude 'tests/' \
  --exclude '__pycache__/' \
  ./ <REACHY_USER>@<REACHY_HOST>:<PROJECT_DIR_ON_REACHY>/
```

---

## トラブルシュート（最小）

- `ssh: connect to host ... timed out`
  - Reachy Mini とPCが同一ネットワークか確認
  - IPの再確認

- `ModuleNotFoundError: No module named 'reachy_mini'`
  - Python環境が違う可能性あり。`which python3` を確認

- 音が出ないがエラーもない
  - 本体の音量設定
  - スピーカー接続状態
  - 実機側ログ確認

- `Timeout while waiting for connection with the server`
  - `sudo systemctl status reachy-mini-daemon --no-pager -l`
  - `curl -s http://localhost:8000/api/daemon/status`
  - `curl -i http://localhost:8000/api/state/full`
  - 必要なら `sudo systemctl restart reachy-mini-daemon`
  - アプリ側で待機値を上げる:
    - `REACHY_CONNECT_TIMEOUT_SEC=90`
    - `REACHY_CONNECT_RETRIES=5`
    - `REACHY_CONNECT_RETRY_INTERVAL_SEC=5`
