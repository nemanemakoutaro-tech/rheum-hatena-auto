# 膠原病・リウマチ一人抄読会 → はてなブログ自動投稿

## 月500円程度を目標にした低コスト設定

この版はデフォルトで `gpt-5.4-mini` を使用し、Web検索を1実行あたり最大2 tool calls、出力を最大4500 tokens、reasoningを `low` に制限します。

GitHub Actions の Repository Variables で以下を変更できます。通常は変更不要です。

- `OPENAI_MODEL=gpt-5.4-mini`
- `OPENAI_MAX_TOOL_CALLS=2`
- `OPENAI_MAX_OUTPUT_TOKENS=4500`
- `OPENAI_SEARCH_CONTEXT=medium`
- `OPENAI_REASONING_EFFORT=low`
- `OPENAI_VERBOSITY=medium`

OpenAIの現行公表価格では、gpt-5.4-mini は入力 $0.75 / 1M tokens、出力 $4.50 / 1M tokens、Web search は $10 / 1,000 calls（加えて検索内容tokenがモデル料金で課金）です。毎日1回・最大2検索に制限しているため、通常利用では月500円前後を狙える設計ですが、為替・実際のtoken量・価格改定により保証はできません。OpenAI Platform側で月次予算アラート/使用上限も設定してください。

品質を上げたい場合は `OPENAI_REASONING_EFFORT=medium`、または `OPENAI_MODEL=gpt-5.6-terra` に変更できますが、料金は上がります。まずこの設定で1〜2週間運用し、記事品質に不足がある場合だけ上げるのを推奨します。


毎朝、日本時間 08:00 ごろに GitHub Actions が以下を自動実行します。

1. OpenAI Responses API + Web Search で最新エビデンスを検索
2. 日本の実臨床に即した1〜2テーマの記事を生成
3. はてなブログ AtomPub API で公開（または下書き）
4. `articles/YYYY-MM-DD.md` と `history.json` をGitHubへ保存
5. 過去記事タイトルを次回生成時に渡し、テーマ重複を減らす

## 1. GitHubにアップロード

このフォルダの中身を新しいGitHub repositoryへアップロードしてください。
Private repositoryでも構いません。

## 2. GitHub Secretsを登録

Repository → **Settings → Secrets and variables → Actions → Secrets** に以下を登録します。

| Secret | 内容 |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key |
| `HATENA_ID` | はてなID |
| `HATENA_API_KEY` | はてなブログ「詳細設定 → AtomPub」に表示されるAPIキー |

**はてなの通常ログインパスワードは保存しないでください。**

## 3. Repository Variables（任意）

同じ画面の **Variables** で設定できます。

| Variable | 既定値 | 説明 |
|---|---|---|
| `HATENA_BLOG_ID` | `ctd-gim.hatenablog.com` | 投稿先ブログID。独自ドメイン利用時もAtomPubに表示される元ブログIDを指定 |
| `HATENA_DRAFT` | `no` | `yes` なら下書き、`no` なら公開 |
| `OPENAI_MODEL` | `gpt-5` | 使用するOpenAI APIモデル |

最初のテストでは `HATENA_DRAFT=yes` を推奨します。問題なければ `no` に変更してください。

## 4. 初回テスト

GitHub repositoryの **Actions → Daily Rheumatology Hatena Post → Run workflow** を押します。

成功すると、はてなブログに記事が作成され、同時に `articles/` と `history.json` が自動更新されます。

## 5. 投稿時刻

`.github/workflows/daily-hatena.yml` は

```yaml
- cron: "0 23 * * *"
```

です。GitHub ActionsのcronはUTCなので、これは **毎日08:00 JST** に相当します。

GitHubのscheduled workflowは混雑状況により数分〜数十分遅れる場合があります。厳密な08:00:00実行を保証する仕組みではありません。

## 記事方針を変更する

`prompt.md` を編集してください。疾患、記事構成、日本の実臨床への適合性などのルールを一か所に集約しています。

## セキュリティ

- API keyはコードや`history.json`に書きません。
- GitHub Secretsから実行時だけ環境変数として渡します。
- はてなAtomPubはHTTPS endpointを使用します。
- はてな認証には、通常パスワードではなくAtomPub APIキーを使用します。

## 費用

OpenAI APIの利用料金が毎日の記事生成ごとに発生します。ChatGPT Plus等の契約とは別課金です。

## 重複回避

`history.json` の直近40投稿のタイトルを毎回モデルへ渡します。完全な意味的重複除去ではありませんが、同じテーマの反復をかなり減らせます。過去1年分（最大365件）を保存します。

## ファイル構成

```text
.github/workflows/daily-hatena.yml
scripts/generate_article.py
scripts/post_hatena.py
prompt.md
requirements.txt
history.json
articles/
README.md
```
