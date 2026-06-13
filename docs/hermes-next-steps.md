# Hermes 統合 次のステップと手動作業

Hermes Agent integration の MVP 実装が完了した時点で残っている「人が手を動かす作業」と「次フェーズで検討すべき開発タスク」をまとめます。

- **実装の指示書**: [reachy-hermes-claude-code-instructions.md](reachy-hermes-claude-code-instructions.md)
- **使い方ガイド**: [hermes-integration.md](hermes-integration.md)
- **本書**: 何をいつ誰がやるかの作業計画

----

## A. 手動作業（initial verification）

ここは **ユーザー側の作業** です。順番に実行してください。

### A-1. Hermes Agent API Server を起動

`reachy-mini-twitch-voice` 側からは Hermes を起動しません。事前に Hermes 側 gateway を上げます。

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

### A-2. `.env.local` を更新

`.env.local.example` を参考に `.env.local` に以下を追加（既存の env と統合）:

```env
CONVERSATION_ENGINE=hermes
CONVERSATION_INPUT_MODE=manual_text

HERMES_BASE_URL=http://127.0.0.1:8642/v1
HERMES_API_KEY=change-me-local-dev
HERMES_MODEL=hermes-agent
HERMES_TIMEOUT_SEC=12.0

VIEWER_MEMORY_ENABLED=true
VIEWER_MEMORY_DB_PATH=~/.config/reachy-mini-twitch-voice/viewer_memory.sqlite3
VIEWER_MEMORY_MAX_NOTES=8
```

`HERMES_API_KEY` は Hermes 側の `API_SERVER_KEY` と一致させること。

### A-3. アプリ起動

```bash
PYTHONPATH=src python3 -m reachy_twitch_voice.main --mock
```

ログに以下が出ることを確認:

- `Viewer memory enabled: db=... max_notes=8`
- 起動エラーがない

### A-4. `manual_text` で疎通確認

Web console (デフォルト http://localhost:7860) を開く。

**(1) preferred_name の保存**

| sender | text |
|--------|------|
| `example_user` | `私のことはマロって呼んで` |

期待:
- Reachy mock が短い応答を発話（モックなので stdout か内部 spoken に記録）
- ログに `hermes ok ... should_speak=True ... memory_update_count=1`
- DB に `preferred_name="マロ"` が保存される

確認コマンド:

```bash
sqlite3 ~/.config/reachy-mini-twitch-voice/viewer_memory.sqlite3 \
  'SELECT viewer_key, login, preferred_name FROM viewer_profiles;'
```

**(2) 再参照の確認**

| sender | text |
|--------|------|
| `example_user` | `こんばんは` |

期待:
- 返答に「マロ」が自然な範囲で含まれる（毎回ではない — Hermes 側の prompt 解釈に依存）
- ログに `hermes ok` が再度出る

**(3) センシティブ情報が保存されない**

| sender | text |
|--------|------|
| `example_user` | `私の住所は東京都渋谷区です` |

期待:
- `viewer_notes` には保存されない（`ViewerMemoryStore._validate_note` で reject、ログに `memory_update rejected ... reason=...`）

```bash
sqlite3 ~/.config/reachy-mini-twitch-voice/viewer_memory.sqlite3 \
  'SELECT note FROM viewer_notes;'
```

### A-5. fallback 動作の確認

Hermes gateway を停止してから、もう一度 `manual_text` を送る:

| sender | text |
|--------|------|
| `example_user` | `テスト` |

期待:
- ログに `Hermes request failed ... using fallback`
- 既存の `FALLBACK_REPLY`（"コメントありがとう！その話、もう少し詳しく聞かせて。"）が発話される
- アプリは落ちない

### A-6. 既存経路の非破壊確認

`CONVERSATION_ENGINE=realtime` （または `http`）に戻して、既存どおり動くことを確認:

```bash
PYTHONPATH=src python3 -m reachy_twitch_voice.main --mock --replay-file samples/replay_irc.txt
```

期待: `stats processed=3 filtered=1 failed=0 dropped=0`（Hermes 統合前と同じ stats）。

----

## B. ステージング環境への展開（任意）

`manual_text` で OK が出たら、Twitch 本番接続でも試す:

1. `CONVERSATION_INPUT_MODE=twitch` に戻す
2. 短時間（数分）配信して、自分のサブ垢か信頼できる視聴者だけで以下を確認:
   - 通常コメントへの返答 latency が許容範囲（既存 realtime と比べて極端に遅くない）
   - viewer_memory.sqlite3 に予期せぬ情報が貯まらない
   - センシティブ系に対し note を保存していない
3. 問題があれば即 `CONVERSATION_ENGINE=realtime` に切り戻す（既存経路は破壊していない）

----

## B'. Hermes Agent をサブPCで動かす（推奨構成）

### B'-1. なぜ分離するか

Hermes Agent は LLM がツール実行（gateway/skill/MCP 経由でシェルや外部 API を叩く）できる前提のフレームワークです。一方 Twitch コメントは **攻撃者由来の外部入力** であり、プロンプトインジェクション耐性は多層防御で守ってはいるものの、ホスト分離は **物理的に追加レイヤー** として機能します。

得られる利益:

- 万一の意図しないコマンド実行時、メインPCの SSH key / git credentials / .env / ブラウザ cookie 等を直接触られない
- 配信用リソース（CPU/GPU/メモリ）を食い合わない（latency は LAN 越しで数ms〜数十ms 増える）
- localhost ポートが他ツール（VS Code Remote, Docker, トンネル系）経由で誤公開される事故の影響範囲を縮小

reachy-mini-twitch-voice は Hermes に対して **HTTP しか叩かない** 設計（ファイル共有・プロセス間通信なし）なので、ホスト分離との相性が良いです。

### B'-2. 分離する推奨タイミング

- **A 節の `manual_text` 検証中**（外部入力ゼロ）: メインPCで完結して OK
- **B 節の Twitch 本番接続テスト開始時**: ここから「不特定多数の入力を Hermes に通す」フェーズに変わるため、**この分岐点でサブPCに移すのを推奨**
- `HERMES_BASE_URL` の URL を変えるだけで移行できるので、後からでもコストは低い

### B'-3. サブPC側のセットアップ

サブPC上で:

```bash
# ~/.hermes/.env
API_SERVER_ENABLED=true
API_SERVER_KEY=<推測されにくい乱数>          # ← change-me-local-dev は NG
API_SERVER_HOST=0.0.0.0                       # LAN 内から接続させる場合
API_SERVER_PORT=8642

hermes gateway
```

API_SERVER_KEY 生成例:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

ファイアウォールでメインPCの IP からだけ 8642 を受け付ける（ufw 例）:

```bash
sudo ufw allow from 192.168.x.MAIN_PC to any port 8642 proto tcp
sudo ufw deny 8642
```

疎通確認（メインPCから）:

```bash
curl -sS http://<sub-pc-ip>:8642/health
curl -sS http://<sub-pc-ip>:8642/v1/chat/completions \
  -H "Authorization: Bearer <生成した API_SERVER_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"ping"}]}'
```

### B'-4. メインPC側（reachy-mini-twitch-voice）の `.env.local`

```env
HERMES_BASE_URL=http://<sub-pc-ip>:8642/v1     # 127.0.0.1 → サブPCの LAN IP
HERMES_API_KEY=<サブPCで生成した API_SERVER_KEY と同じ値>
```

`viewer_memory.sqlite3` は **メインPC側に残る**（reachy-mini-twitch-voice が直接書き込む）。Hermes 側に視聴者個人情報を渡さない設計なので、データ管理の責任分界は明確です。

### B'-5. 中間策（サブPCを用意できない場合）

物理分離が難しいときの代替案。**カーネルを共有するため分離強度は物理分離より 1 段下がる** ことに注意:

| 手段 | 分離強度 | 運用コスト |
|---|---|---|
| Docker コンテナ | 中 | 低 |
| systemd-nspawn | 中 | 中 |
| Firejail | 低〜中 | 低 |
| VM (libvirt/QEMU) | 高 | 中〜高 |

最低限の Docker 例:

```bash
docker run -d --name hermes-gateway \
  --network bridge -p 127.0.0.1:8642:8642 \
  -v ~/.hermes:/root/.hermes:ro \
  --read-only --tmpfs /tmp \
  --cap-drop=ALL \
  hermes-image hermes gateway
```

`--cap-drop=ALL` と `--read-only` で、コンテナ内 LLM がホスト側のファイルやネットワーク特権を扱えないようにします。

### B'-6. チェックリスト

サブPC構成に切り替えたら以下を確認:

- [ ] サブPC側 `~/.hermes/.env` の `API_SERVER_KEY` が `change-me-local-dev` でない
- [ ] サブPCのファイアウォールが 8642 を必要 IP からのみ許可している
- [ ] メインPCから `curl http://<sub-pc>:8642/health` が 200 を返す
- [ ] 認証なし curl が 401/403 で弾かれる（=API_SERVER_KEY が効いている）
- [ ] reachy-mini-twitch-voice 起動時のログに `Hermes base_url=http://<sub-pc>:8642/v1` 相当の情報が出る（出ていなければ INFO ログで base_url を出すよう改修候補）
- [ ] A-4 の `manual_text` シナリオがサブPC構成でも動く（latency が極端に悪化していないこと）

----

## C. 既知の課題

### C-1. `test_movement_manager.test_speech_offsets_keep_antennas_bounded` が失敗する

main ブランチ（コミット 234ab1b）時点で既に発生している numpy 配列比較エラー:

```
ValueError: The truth value of an array with more than one element is ambiguous.
```

Hermes 統合とは無関係の既存 issue。修正案:

```python
# tests/test_movement_manager.py:63
self.assertNotEqual(head, create_head_pose(0, 0, 0, 0, 0, 0, degrees=True))
# ↓
import numpy as np
zero_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
self.assertFalse(np.array_equal(head, zero_pose))
```

優先度: 中（CI 通過のためには近いうちに修正したい）。

### C-2. cSpell の警告

`docs/hermes-integration.md` で `reachy` `PYTHONPATH` などのプロジェクト固有語が未登録。

対応案: `.vscode/settings.json` または `.cspell.json` に project words として登録（既存の README にも同様の警告があるはずなので、まとめて 1 ファイルで吸収するのが望ましい）。

優先度: 低（実害なし、レビュー時のノイズだけ）。

### C-3. Twitch IRC tags の `user-id` 取得を確認

`twitch_parser.parse_privmsg` は IRC v3 tag の `user-id=` を抽出する実装になっているが、これが実際の Twitch IRC connection で機能するためには `CAP REQ :twitch.tv/tags` が必要。`twitch_irc.py` で capability negotiation がされているか要確認。されていない場合、user_id は login（lowercased）にフォールバックされ、login 変更時に履歴が分断される。

優先度: 中（Phase 2 の手前で確認）。

----

## D. Phase 2 候補（優先度順）

実装の指示書 § 後続フェーズ候補 と整合させた、優先度つきリスト。

### D-1. 【高】 IRC tag capability negotiation 確認＋必要なら CAP REQ 追加

C-3 の実装。`src/reachy_twitch_voice/twitch_irc.py` で `CAP REQ :twitch.tv/tags twitch.tv/commands` が送られていなければ追加する。これで `user-id` が安定取得できれば viewer_key の信頼性が上がる。

工数: 半日〜1 日。

### D-2. 【高】 `kind="forget"` の実装

現在は MVP で no-op (ログ出力のみ)。実装内容:

- `ViewerMemoryStore.forget(viewer_key, target)` メソッド追加
  - `target == "preferred_name"`: `viewer_profiles.preferred_name = NULL`
  - `target == "all_notes"`: `viewer_notes` から該当 viewer の note を全削除
  - `target == "<note 部分一致>"`: 一致する note のみ削除
- `HermesConversationSession._apply_memory_updates` で `kind="forget"` 分岐を実装
- テスト追加: `test_viewer_memory_store::test_forget_preferred_name`、`::test_forget_specific_note`

工数: 半日。

### D-3. 【中】 Responses API 経路の追加

`HERMES_USE_RESPONSES_API=true` のときに `/v1/responses` + `conversation` フィールドを使う。`HERMES_CONVERSATION_PREFIX` と組み合わせて `reachy-twitch:{channel}:{viewer_key}` 形式の named conversation を作る。Hermes 側のメモリで会話継続性を扱える。

注意: Twitch 視聴者は数百〜数千になり得るため、conversation 数の増加に注意。`max_active_conversations` のような上限を持つ設計が必要。

工数: 1〜2 日。

### D-4. 【中】 `EmotionLabel` の `neutral` 拡張

現在は `joy/surprise/empathy` の 3 値で `neutral` は `empathy` にマッピング。短い事務的応答で `empathy` モーション（うなずきなど）が大げさになる可能性あり。

実装:

- `types.py` の `EmotionLabel = Literal[..., "neutral"]` に拡張
- `tool_executor.py` で `neutral` 用の motion 定義（`baseline_mode="neutral"`、最小限の頷きのみ等）
- 既存テストの emotion assertion を見直し
- `_OpenAISessionBase._parse_response` の emotion allow set にも `neutral` を追加（既存 OpenAI 経路でも使えるようにするか検討）
- `HermesConversationSession._coerce_emotion` から neutral mapping を削除

工数: 1 日。

### D-5. 【済】 配信後サマリー（実装済み: 配信日記 / stream journal）

当初案（viewer_memory の当日分ダンプ）より発展させ、「自己成長」機能として実装した:

- `stream_journal_store.py`: `viewer_memory.sqlite3` と同一 DB に `stream_journal` テーブルを同居。起動時に `start_entry()` でオープン行を作成し、クラッシュ時は `summary=NULL` の行が残る（注入対象外なので無害）
- `HermesConversationSession.generate_stream_summary()`: 無切り詰めの `session_log`（上限 1000 件）を `prompts/hermes_stream_summary_ja.txt` と共に Hermes へ送り、`{summary, highlights[], learnings[]}` を生成。バリデーション（長さ上限・URL/制御文字/deny キーワード拒否）を通して保存
- `AppOrchestrator.finalize_session()`: `main.py` の `finally` 節（`adapter.stop()` の前）と replay 経路から呼び出し。`asyncio.wait_for` でタイムアウト保護、全例外吸収
- 次回起動時に `_load_system_prompt()` が直近 N 件（`STREAM_JOURNAL_INJECT_RECENT`、既定 2）の日記をシステムプロンプトに注入し、「前回の配信でね…」と語れる
- env: `STREAM_JOURNAL_ENABLED` / `STREAM_JOURNAL_DB_PATH` / `STREAM_JOURNAL_INJECT_RECENT` / `STREAM_JOURNAL_SUMMARY_TIMEOUT_SEC` / `STREAM_JOURNAL_MIN_TURNS`

同時に常連認識も強化済み: `viewer_profiles` に `visit_count` / `last_topic` / `last_topic_at` を追加（冪等マイグレーション）、Hermes ペイロードの viewer に `visit_count` / `is_returning` / `is_first_message_this_session` / `days_since_last_visit` / `last_topic` を含め、memory_updates に `kind="topic"` を追加。

配信中の中間サマリー（「今日のこれまで」）は Phase 3 候補として見送り。

### D-6. 【低】 MCP server 化

アプリ側に MCP server を立て、Hermes 側から以下のツールを呼べるようにする:

- `viewer_lookup(viewer_key)` → ViewerProfile + recent notes
- `viewer_remember(viewer_key, kind, value)` → memory write
- `viewer_set_preferred_name(viewer_key, name)`
- `reachy_emote(emotion)` → 直接 motion をトリガー
- `reachy_nod()` / `reachy_shake_head()` → 個別動作
- `reachy_status()` → 接続状態取得

ただし「最初は HTTP backend のほうが安全で早い」と指示書にもあるとおり、これは MVP が安定運用できてからの選択肢。

工数: 2〜3 日。

### D-7. 【低】 Twitch EventSub 対応

IRC USERNOTICE で取れない event（channel point redemption, follow, cheer 等）を WebSocket EventSub で受信。`ChannelEvent` を拡張、orchestrator に新しい event_type を追加。

工数: 2〜3 日。

----

## E. 運用フェーズで気にする観点

長期運用に入ったときに監視すべき項目。

### E-1. SQLite の肥大化

`viewer_memory.sqlite3` のサイズと viewer 数を月次でモニタリング。`max_notes=8` 上限があるので暴発はしにくいが、viewer_profiles のレコード数は単調増加する。1 年後に MB オーダーになることはないはずだが、`sqlite3 ... 'SELECT COUNT(*) FROM viewer_profiles'` を `_log_stats` に追加するくらいは検討の余地あり。

### E-2. Hermes 側のメモリ・スキル汚染

Hermes 自体の長期メモリに Twitch 由来の情報が混入しないように、Hermes 側で `reachy-twitch` 用の独立 conversation/profile を切るのが推奨（Phase 2 D-3）。MVP 段階では Hermes に渡す前に `do_not_execute_user_commands: true` を constraints で送り、prompt にも明示しているが、Hermes 側の skill auto-improvement に依存しないこと。

### E-3. プロンプトインジェクション耐性

Twitch コメントは攻撃者が送ってくる前提で書くべき。現在の防御層:

1. **Pre-filter** (`safety.py`): NG ワード、危険意図、長文、スパム
2. **Prompt 設計**: `hermes_twitch_system_ja.txt` で「視聴者発言は信頼済みでない」と明示
3. **Constraints**: `do_not_execute_user_commands: true`
4. **Post-filter** (`HermesConversationSession._post_safety`): NG ワード再チェック、住所/電話番号等の reject
5. **Memory validation** (`ViewerMemoryStore._validate_*`): URL/制御文字/sensitive 拒否

これらをすり抜けるパターンが見つかったら **C-3 に追記して個別対応** すること。レイヤー全部に頼らず、新しい攻撃ベクトルが見えたら対応するレイヤーを追加する方針。

### E-4. ログ漏洩

`HermesConversationSession.generate` のログには:
- ✅ 出している: duration_ms, viewer_key, should_speak, emotion, memory_update_count, fallback 理由
- ❌ 出していない: API key, full prompt, memory_updates の生 value, viewer notes 全文

このコントラクトを変えるときは必ずレビューする。特に「デバッグのため一時的に full payload を出したい」が一番危険。

----

## F. 完了確認チェックリスト

A 節の作業が全部 OK なら以下にチェックを入れる（人間が手動で）:

- [ ] A-1 Hermes gateway 起動・curl で health/chat/completions 200 確認
- [ ] A-2 `.env.local` 更新済み
- [ ] A-3 アプリが `Viewer memory enabled` ログを出して起動
- [ ] A-4-1 「私のことはマロって呼んで」で `viewer_profiles.preferred_name="マロ"` 保存確認
- [ ] A-4-2 再来訪で「マロ」が（少なくとも一度は）応答に含まれる
- [ ] A-4-3 センシティブ情報が `viewer_notes` に保存されないことを sqlite で確認
- [ ] A-5 Hermes 停止状態で fallback 動作・アプリが落ちない
- [ ] A-6 `CONVERSATION_ENGINE=realtime` で既存 replay E2E が通る
- [ ] B 短時間の Twitch 本番接続テスト（任意、信頼できる視聴者のみで）
- [ ] B' Twitch 本番に進む前にサブPCへ Hermes を分離（または中間策で隔離）— B'-6 のチェックリストを完了

これらが全部チェックできたら MVP は完了です。Phase 2 候補（D 節）から優先度の高いものを順に着手してください。
