# Hermes Agent 統合ガイド

`reachy-mini-twitch-voice` は `CONVERSATION_ENGINE=hermes` で **Hermes Agent API Server** を会話バックエンドとして利用できます。現在はこちらが**メイン構成**です（サブ PC 上の Hermes を LAN 経由で利用。`.env.local.example` 参照）。既存の `realtime` / `http`（OpenAI 系）経路は切り戻し用にそのまま残ります。

このドキュメントは:

- 環境変数の意味と既定値
- Hermes Agent API Server の起動・疎通確認
- アプリ側の動作確認手順（`manual_text` + `--mock`）
- 視聴者メモリ（`ViewerMemoryStore`）の挙動

をまとめたものです。

## アーキテクチャ概要

```
Twitch IRC / Web console (manual_text)
        │
        ▼
 Orchestrator ── Safety filter ──▶ HermesConversationSession ──▶ Hermes Agent API Server
        │                                  │                          (OpenAI-compatible)
        │                                  │
        │                                  ▼
        │                          ViewerMemoryStore (SQLite)
        │
        ▼
   Reachy adapter (TTS / motion / mock)
```

- **Hermes** には structured JSON event を `user` メッセージで渡し、JSON 形式のレスポンスを期待します。
- **ViewerMemoryStore** は Twitch 視聴者ごとの `preferred_name`・短い `note`・来訪回数（`visit_count`）・前回の話題（`last_topic`、memory_updates の `kind="topic"` で更新）を SQLite に保存します。
- **StreamJournalStore** は配信終了時に Hermes が生成したサマリー（summary / highlights / learnings）を同じ SQLite に保存し、次回起動時にシステムプロンプトへ注入します。

## 環境変数

`.env.local.example` の Hermes / Viewer memory セクション参照。デフォルト値は `HermesConfig` / `ViewerMemoryConfig` に対応しています。

| 変数 | 既定値 | 備考 |
|------|--------|------|
| `CONVERSATION_ENGINE` | `realtime` | `hermes` を指定すると Hermes 経路に切替 |
| `HERMES_BASE_URL` | `http://127.0.0.1:8642/v1` | `/v1` まで含む |
| `HERMES_API_KEY` | (空) | Hermes 側 `API_SERVER_KEY` と一致させる |
| `HERMES_MODEL` | `hermes-agent` | Hermes のモデル識別子 |
| `HERMES_TIMEOUT_SEC` | `12.0` | 1 リクエストのタイムアウト |
| `HERMES_STREAM` | `false` | MVP では未使用（将来用） |
| `HERMES_USE_RESPONSES_API` | `false` | Phase 2 候補 |
| `HERMES_CONVERSATION_PREFIX` | `reachy-twitch` | Phase 2 候補（named conversation） |
| `HERMES_SYSTEM_PROMPT_FILE` | (空) | Hermes 専用 prompt の override |
| `HERMES_RETRY_COUNT` | `1` | リクエスト失敗時の再試行回数（0.5s 間隔） |
| `STREAM_JOURNAL_ENABLED` | `true` | 配信日記（終了時サマリー生成 + 次回プロンプト注入） |
| `STREAM_JOURNAL_DB_PATH` | viewer_memory と同じ | `stream_journal` テーブルを同一 DB に同居 |
| `STREAM_JOURNAL_INJECT_RECENT` | `2` | 次回起動時にシステムプロンプトへ注入する直近日記数 |
| `STREAM_JOURNAL_SUMMARY_TIMEOUT_SEC` | `20.0` | シャットダウン時のサマリー生成タイムアウト |
| `STREAM_JOURNAL_MIN_TURNS` | `3` | これ未満のターン数ならサマリー生成をスキップ |
| `VIEWER_MEMORY_ENABLED` | `true` | `false` なら no-op store |
| `VIEWER_MEMORY_DB_PATH` | `~/.config/reachy-mini-twitch-voice/viewer_memory.sqlite3` | `~` 展開可 |
| `VIEWER_MEMORY_MAX_NOTES` | `8` | viewer 1 人あたりの note 上限 |
| `VIEWER_MEMORY_SAVE_SOURCE_MESSAGE` | `false` | true なら note 保存時に message_id も記録 |

## Hermes Agent API Server の起動

`reachy-mini-twitch-voice` は Hermes を**起動しません**。事前に Hermes 側で gateway を上げてください。

例:

```bash
# ~/.hermes/.env
API_SERVER_ENABLED=true
API_SERVER_KEY=change-me-local-dev
# default: API_SERVER_HOST=127.0.0.1
# default: API_SERVER_PORT=8642

hermes gateway
```

疎通確認:

```bash
curl -sS http://127.0.0.1:8642/health
curl -sS http://127.0.0.1:8642/v1/chat/completions \
  -H "Authorization: Bearer change-me-local-dev" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"ping"}]}'
```

## アプリ側の動作確認 (manual_text + --mock)

```bash
export CONVERSATION_ENGINE=hermes
export CONVERSATION_INPUT_MODE=manual_text
export HERMES_BASE_URL=http://127.0.0.1:8642/v1
export HERMES_API_KEY=change-me-local-dev
export HERMES_MODEL=hermes-agent
export VIEWER_MEMORY_ENABLED=true

PYTHONPATH=src python3 -m reachy_twitch_voice.main --mock
```

Web console (デフォルト http://localhost:7860) から:

1. `sender: example_user`, `text: 私のことはマロって呼んで` を送信
   → `viewer_memory.sqlite3` の `viewer_profiles.preferred_name == "マロ"` が保存される
2. 同じ sender で `text: こんばんは` を送信
   → 返答に「マロ」が自然な範囲で含まれる（毎回ではない）
3. `text: 住所は東京都...` のようなセンシティブ系
   → `viewer_notes` には保存されない（`ViewerMemoryStore._validate_note` で reject）

Hermes 未起動・接続失敗時はアプリは落ちず、`FALLBACK_REPLY` で発話します。

## メモリ保存のルール

`HermesConversationSession` は Hermes 応答の `memory_updates` を以下のとおり validate します:

- **preferred_name**:
  - 32 文字以下
  - URL（`http://`, `https://`）を含まない
  - 制御文字を含まない
  - `_DENY_KEYWORDS`（個人情報 / 住所 / 電話番号 / 差別 / 暴力）を含まない
- **note**:
  - 200 文字以下
  - `confidence >= 0.4`
  - URL / 制御文字 / 危険キーワード不可
  - `viewer_key` ごとに `VIEWER_MEMORY_MAX_NOTES` を超えないよう古いものから自動削除
  - 重複 note は `updated_at` のみ更新
- **forget**: 現フェーズでは無視（Phase 2 で実装予定）

## 視聴者キー (viewer_key)

`HermesConversationSession._viewer_key()`:

1. `event.user_id`（IRC tags の `user-id`、数値）が取得できればそれを使う（login 変更後も履歴を保持）
2. 取得できない場合は `event.user_name`（lowercased login）にフォールバック
3. それも空なら `"unknown"`

`manual_text` 入力では `user_id` が無いため、sender 名（lowercased）が viewer_key になります。

## ログ

INFO レベルで以下を出します（API キー・全 prompt は出しません）:

- `hermes ok duration_ms=... viewer_key=... should_speak=... emotion=... memory_update_count=... fallback_used=False`
- `Hermes request failed viewer_key=... err=...; using fallback`
- `Skipping speech: empty reply_text id=... emotion=...`（`should_speak=false` 時）
- `viewer_memory: set_preferred_name viewer_key=... len=... reason=...`
- `viewer_memory: add_note viewer_key=... len=... confidence=...`

## トラブルシューティング

| 症状 | 確認ポイント |
|------|-------------|
| 常に `FALLBACK_REPLY` になる | `HERMES_API_KEY` 設定 / Hermes 側 `/health` が 200 か |
| viewer_memory.sqlite3 が作られない | `VIEWER_MEMORY_ENABLED=true` か / DB 親ディレクトリの書込権限 |
| preferred_name が保存されない | Hermes 応答が `memory_updates` を返しているか / `_validate_preferred_name` で reject されていないか（INFO ログ参照） |
| 同じ user で履歴が断絶 | IRC tags が無いと viewer_key が login 依存。Twitch 側で IRCv3 capability `twitch.tv/tags` が有効か確認 |

## Phase 2 候補

- `HERMES_USE_RESPONSES_API=true` 経路（`/v1/responses` + `conversation` named conversation）
- `kind="forget"` の実装（preferred_name クリア + 該当 note 削除）
- Twitch 本番 IRC E2E（流量負荷確認）
- MCP server 化（`viewer_lookup` / `viewer_remember` / `reachy_emote` などのツール）
- `EmotionLabel` を `neutral` 含む 4 値に拡張し motion を追加
