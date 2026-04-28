# Reachy Mini Twitch Voice

Reachy Mini が Twitch チャットをリアルタイム受信して、日本語で読み上げるアプリです。

コマンド例の `<PROJECT_DIR>` / `<PROJECT_DIR_ON_REACHY>` / `<REACHY_USER>` / `<REACHY_HOST>` は実環境に合わせて置き換えてください。

## Features

- Twitch IRC 受信（TLS, 自動再接続）
- コメント正規化（URL置換、連投圧縮）
- 厳格フィルタ（NGワード、危険意図、スパム、長文）
- OpenAI会話生成（返事 + 話題展開 + 感情ラベル）
- FIFO（到着順）での発話処理
- 発話タイムアウトによる詰まり回避
- Reachy SDKアダプタとMockアダプタの切替
- GradioベースのWebコンソールで人格/プロンプトを保存・即時反映
- Webコンソールから入力参照モードを `twitch` / `manual_text` で即時切替

## Environment Variables

- `TWITCH_CHANNEL` (required)
- `TWITCH_OAUTH_TOKEN` (required): `oauth:` あり/なしどちらでも可
- `TWITCH_NICK` (required)
- `OPENAI_API_KEY` (required)
- `OPENAI_REALTIME_MODEL` (optional, default: `gpt-4o-mini`)
- `CONVERSATION_ENGINE` (optional, default: `realtime`, values: `realtime|http`)
- `CONVERSATION_INPUT_MODE` (optional, default: `twitch`, startup mode: `twitch|manual_text`)
- `TWITCH_MESSAGE_CONTEXT_WINDOW` (optional, default: `30`)
- `OPENAI_TIMEOUT_SEC` (optional, default: `10.0`)
- `PERSONA_NAME` (optional, default: `NUVA`)
- `PERSONA_NAME_KANA` (optional, default: `ヌーバ`)
- `OPERATOR_NAME` (optional, default: `にかなとむ(tom_t100ta)`)
- `PERSONA_STYLE` (optional, default: `親しみを保ちつつ、常に適度に礼儀正しく`)
- `SYSTEM_PROMPT_FILE` (optional, default: empty = `src/reachy_twitch_voice/prompts/system_ja.txt`)
- `OPERATOR_USERNAMES` (optional, default: `tom_t100ta,にかなとむ`)
- `PROFILE_STORAGE_DIR` (optional, default: `~/.config/reachy-mini-twitch-voice/profiles`)
- `ACTIVE_PROFILE` (optional, default: empty = 保存済みアクティブプロフィールを優先)
- `ENABLE_TOOLS` (optional, default: `true`): OpenAI ネイティブ function calling を有効化（`robot_action`, `get_twitch_info` ツール）
- `ENABLE_WEB_SEARCH` (optional, default: `false`): OpenAI 組み込み `web_search_preview` ツールを有効化（遅延増加に注意）
- `NG_WORDS` (optional, comma-separated)
- `MAX_CHARS` (optional, default: `140`)
- `SPAM_WINDOW_SEC` (optional, default: `5`)
- `MESSAGE_TIMEOUT_MS` (optional, default: `15000`)
- `RECONNECT_MAX_SEC` (optional, default: `30`)
- `IDLE_MOTION_ENABLED` (optional, default: `true`)
- `IDLE_INTERVAL_SEC` (optional, default: `3`)
- `MAX_QUEUE_SIZE` (optional, default: `100`)
- `MAX_QUEUE_WAIT_MS` (optional, default: `60000`, `0` = disable stale-drop)
- `QUEUE_DROP_POLICY` (optional, default: `drop_oldest`)
- `REACHY_TTS_ENGINE` (optional, default: `espeak-ng`)
- `REACHY_TTS_LANG` (optional, default: `ja`)
- `REACHY_TTS_OPENAI_MODEL` (optional, default: `gpt-4o-mini-tts`)
- `REACHY_TTS_OPENAI_VOICE` (optional, default: `alloy`)
- `REACHY_TTS_OPENAI_FORMAT` (optional, default: `wav`)
- `REACHY_TTS_OPENAI_SPEED` (optional, default: `1.15`)
- `REACHY_GESTURE_ENABLED` (optional, default: `true`)
- `REACHY_SPEECH_MOTION_ENABLED` (optional, default: `true`)
- `REACHY_EXECUTION_HOST` (optional, default: `on_reachy`)
- `REACHY_CONNECTION_MODE` (optional, default: `auto`)
- `REACHY_EXECUTION_HOST=on_reachy`: Reachy本体(Raspberry Pi)で実行
- `REACHY_HOST` (optional, default: `reachy-mini.local`, `network/auto` fallbackで使用)
- `REACHY_AUDIO_VOLUME` (optional, `0-100`)
- `REACHY_HEALTHCHECK_URL` (optional, default: `http://localhost:8000/api/state/full`)
- `REACHY_CONNECT_TIMEOUT_SEC` (optional, default: `45.0`)
- `REACHY_CONNECT_RETRIES` (optional, default: `3`)
- `REACHY_CONNECT_RETRY_INTERVAL_SEC` (optional, default: `3.0`)
- `IDLE_USE_DOA` (optional, default: `false`)
- `REACHY_MOTION_STYLE` (optional, default: `official`, values: `official|legacy`)
- `REACHY_IDLE_STYLE` (optional, default: `attentive`, values: `attentive|calm`)
- `REACHY_IDLE_FIRST_DELAY_SEC` (optional, default: `3.0`)
- `REACHY_IDLE_GLANCE_INTERVAL_SEC` (optional, default: `10.0`)
- `REACHY_SPEECH_MOTION_SCALE` (optional, default: `0.65`)
- `REACHY_EMOTION_MOTION_ENABLED` (optional, default: `true`)
- `WEB_CONSOLE_ENABLED` (optional, default: `true`)
- `WEB_CONSOLE_HOST` (optional, default: `0.0.0.0`)
- `WEB_CONSOLE_PORT` (optional, default: `7860`)

## Local Env File

`python -m reachy_twitch_voice.main` は、起動時のカレントディレクトリにある `.env.local` をデフォルトで読み込みます。
（CLI引数 `--env-file` で固定パス指定可能、`--no-env-file` で無効化）

```bash
cp .env.local.example .env.local
```

`.env.local` に Twitch/Reachy の値を設定しておけば、毎回 `export` は不要です。
ただし Reachy本体ではプロジェクト外の固定パスに置き、`--env-file` で明示指定する運用を推奨します。

補足:
- キャラクター設定は `.env.local` の `PERSONA_*` と `OPERATOR_NAME` で切り替え可能
- 既定のsystem promptは `src/reachy_twitch_voice/prompts/system_ja.txt` を使用
- カスタムpromptを使う場合のみ `SYSTEM_PROMPT_FILE` を指定（テンプレート変数: `{{PERSONA_NAME}}`, `{{PERSONA_NAME_KANA}}`, `{{OPERATOR_NAME}}`, `{{PERSONA_STYLE}}`）
- Webコンソールで保存したプロフィールは `PROFILE_STORAGE_DIR` 以下に保存される
- `.env.local.example` の既定値は `REACHY_TTS_ENGINE=openai-tts`
- OpenAI TTS をまだ使わない疎通確認では、`REACHY_TTS_ENGINE=espeak-ng` に変えると切り分けしやすい

## 再インストール/再同期時の注意

- `.env.local` は認証情報を含むため、Git管理しない
- PC→本体へ同期するときは `.env.local` を除外する
- 本体側の `.env.local` は固定パスに置いて `--env-file` で読む

```bash
# 例: PC側から本体に同期（認証情報/開発用ファイルは送らない）
rsync -av --delete \
  --exclude '.env.local' \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude 'tests/' \
  --exclude '__pycache__/' \
  ./ <REACHY_USER>@<REACHY_HOST>:<PROJECT_DIR_ON_REACHY>/
```

```bash
# 本体側で初回のみ
mkdir -p ~/.config/reachy-mini-twitch-voice
cp <PROJECT_DIR_ON_REACHY>/.env.local.example ~/.config/reachy-mini-twitch-voice/.env.local
chmod 600 ~/.config/reachy-mini-twitch-voice/.env.local
```

```bash
# 実行時は固定のenvを指定
cd <PROJECT_DIR>
PYTHONPATH=src python3 -m reachy_twitch_voice.main \
  --env-file ~/.config/reachy-mini-twitch-voice/.env.local \
  --log-level INFO
```

## Setup with mise

```bash
cd <PROJECT_DIR>
mise trust
mise install
mise run setup
```

開発ツールを入れる場合:

```bash
mise run dev-setup
```

ロックファイル作成:

```bash
mise run lock
```

注記:
- Python と uv のバージョンは `mise.toml` で管理します。
- Pythonパッケージの解決と `.venv` 作成は uv を使います。
- 実機は `reachy-mini daemon 1.5.0` を前提にします。
- 推奨: `uv pip install -r docs/requirements-reachy.txt`（`reachy-mini==1.5.0`）
- `espeak-ng` が必要です（未導入の場合は `sudo apt install espeak-ng`）。
- より自然な発音は `REACHY_TTS_ENGINE=openai-tts` を推奨します（`OPENAI_API_KEY` 必須）。

## 実機依存セットアップ（固定手順）

標準運用ホストは `on_reachy`（Reachy本体）です。

1. Reachy本体側（SSH）でSDK確認
```bash
python3 -c "import reachy_mini; print('ok')"
```

2. アプリ実行ホスト側で設定
```bash
export REACHY_EXECUTION_HOST=on_reachy
export REACHY_CONNECTION_MODE=auto
export REACHY_GESTURE_ENABLED=true
export REACHY_TTS_ENGINE=openai-tts
export REACHY_TTS_LANG=ja
export REACHY_TTS_OPENAI_MODEL=gpt-4o-mini-tts
export REACHY_TTS_OPENAI_VOICE=alloy
export REACHY_TTS_OPENAI_FORMAT=wav
export REACHY_TTS_OPENAI_SPEED=1.15
export IDLE_USE_DOA=false
```

3. backend稼働判定（`ready` ではなく state API 疎通を使う）
```bash
curl -i http://localhost:8000/api/state/full
```

## Run (mock)

```bash
cd <PROJECT_DIR>
export TWITCH_CHANNEL=your_channel
export TWITCH_OAUTH_TOKEN=xxxxxxxx
export TWITCH_NICK=your_bot_name
mise run run-mock
```

## Run (Reachy SDK)

```bash
cd <PROJECT_DIR>
mkdir -p ~/.config/reachy-mini-twitch-voice
cp .env.local.example ~/.config/reachy-mini-twitch-voice/.env.local
# ~/.config/reachy-mini-twitch-voice/.env.local を編集して値を入れる
PYTHONPATH=src python3 -m reachy_twitch_voice.main \
  --env-file ~/.config/reachy-mini-twitch-voice/.env.local
```

Webコンソールを止めたい場合:

```bash
PYTHONPATH=src python3 -m reachy_twitch_voice.main \
  --env-file ~/.config/reachy-mini-twitch-voice/.env.local \
  --no-web-console
```

既定では `http://<HOST>:7860` でGradio UIが起動します。無認証なので、同一LAN内だけで利用してください。

補足:
- `--env-file` を付けずに起動した場合は、その時点のカレントディレクトリにある `.env.local` だけを読みます
- 起動ログに `Loaded env file: ...` が出ない場合は `.env.local` が読まれていません

Webコンソールでは入力参照モードを即時切り替えできます。

- `twitch`: 従来どおり Twitch IRC から入ったコメントを処理
- `manual_text`: Webコンソールで入力した送信者名と本文を疑似チャットとして投入し、通常の会話生成・感情推定・モーション選択・TTS まで通す

## Twitch Token (Official Device Flow)

`twitchapps.com/tmi` は利用せず、Twitch公式 Device Code Flow を使用します。

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

5. 検証（`oauth:` プレフィックスを除いた生トークンで検証）
```bash
TOKEN="ACCESS_TOKEN"
curl -s -H "Authorization: OAuth ${TOKEN}" https://id.twitch.tv/oauth2/validate
```

6. `{"status":401,"message":"invalid access token"}` の場合
- 失効済みなので Device Flow を再実行して新しい `access_token` を取得
- `.env.local` の `TWITCH_OAUTH_TOKEN=oauth:<new_token>` を更新
- `TWITCH_NICK` がトークン発行ユーザー名と一致しているか確認

## Conversation Behavior

- 入力は Twitch チャットまたは Webコンソールの `manual_text` 疑似チャット入力（マイク入力は無効）
- `CONVERSATION_ENGINE=realtime` は Realtimeスタイルの直列処理（active response競合回避）で動作
- Webコンソールでプロフィールを保存し、`Apply`すると次の会話入力から新しい人格/プロンプトが反映される
- `CONVERSATION_INPUT_MODE` は起動時の初期参照モードで、Webコンソールからランタイム切替できる
- 返答は全コメント対象
- 直近30コメントを文脈として話題を膨らませる
- 感情ラベル（`joy` / `surprise` / `empathy`）を同時生成し、動作へ同期
- `ENABLE_TOOLS=true`（デフォルト）で OpenAI ネイティブ function calling を使用: `robot_action`（ダンス・頭部移動）, `get_twitch_info`（チャンネル情報取得）
- `ENABLE_WEB_SEARCH=true` で Web 検索ツールを追加（OpenAI 組み込み `web_search_preview`）
- `REACHY_MOTION_STYLE=official` では、待機は `attentive -> breathing -> low-frequency phrase` の段階制御で動く
- 発話開始時は emotion に応じた短い公式モーションを優先し、発話中は縮小した speech overlay を重ねる
- 発話終了後は短い settle モーションで姿勢を戻し、急停止感を避ける
- OpenAI呼び出し失敗時は定型文へフォールバックして継続
- キュー混雑時は `drop_oldest` で古いメッセージを間引きし、遅延崩壊を防ぐ
- `OPERATOR_USERNAMES` に一致するユーザー発言は「Operator」として扱う
- チャット無入力が続くと待機モーションを実行する（`IDLE_MOTION_ENABLED=true`）

## Tests

```bash
cd <PROJECT_DIR>
mise run test
```

## Local E2E (no Twitch connection)

```bash
cd <PROJECT_DIR>
mise run replay
```

このモードでは Twitch 認証環境変数が未設定でも動作し、以下を確認できます。
- IRC行パース
- フィルタリング
- FIFO処理
- 統計ログ（`p95_reaction_ms`）

## Pre-hardware Checklist

- ユニットテストが全件成功
- `--replay-file` 実行で例外が出ない
- ログに `stats processed=... filtered=... failed=...` が出る
- `p95_reaction_ms` が想定範囲（目安 1500ms 未満）

## Hardware Verification (required on real robot)

以下は実機でしか確認できません。
- `ReachyMini.media.play_sound()` 経由の再生安定性
- 実スピーカー出力音量・音質
- ジェスチャー同期時の機構安全性
- 実ネットワーク環境での遅延（配信中 p95）

## Notes

`ReachyMiniAdapter` は `reachy-mini==1.5.0` を前提に、`connection_mode=\"auto\"` を標準として接続します。  
発話は `ReachyMini.media.play_sound()` で再生します。音声生成は `openai-tts` または `espeak-ng` を選択できます。

## Reachy接続エラー時（`Timeout while waiting for connection with the server`）

1. Daemon状態を確認
```bash
sudo systemctl status reachy-mini-daemon --no-pager -l
curl -s http://localhost:8000/api/daemon/status
curl -i http://localhost:8000/api/state/full
```

2. 必要なら再起動
```bash
sudo systemctl restart reachy-mini-daemon
```

3. 接続待ちを長くする（`.env.local`）
```env
REACHY_CONNECT_TIMEOUT_SEC=90
REACHY_CONNECT_RETRIES=5
REACHY_CONNECT_RETRY_INTERVAL_SEC=5
```
