# 通し試験の手順（制御ループ → DB → Grafana）

過去の VDIF とモック COSMOS を使って、`mao-45m subref control --database` が
気象・指向・EPL・副鏡の値を DB に書き、Grafana で見えるところまでを確かめる手順。

- 特に断りがなければ、Windows の PowerShell で、リポジトリの一番上のフォルダ（`mao-45m`）から実行する。
- `<パスワード>` は自分の値に置き換える。
- Linux（viento・シャトル PC）での読み替えは最後の節を参照。

## 1. 新しい PC での初回準備

```powershell
git clone -b feature/generic-field-parser https://github.com/kai-stack-eng/mao-45m.git
cd mao-45m
git config user.name "kai-stack-eng"
git config user.email "<数字>+kai-stack-eng@users.noreply.github.com"
uv sync --extra monitor
Copy-Item monitor\.env.example monitor\.env
```

共用の PC では `git config` に `--global` を付けない。GitHub のトークンを git に保存させない。

`monitor\.env` で書き換える・書き足す行:

```
POSTGRES_PASSWORD=<パスワード>
GRAFANA_PASSWORD=<パスワード>
MAO_DATABASE_URL=postgresql://nobeyama:<パスワード>@127.0.0.1:5432/nobeyama
# 3000番が使われているときだけ
GRAFANA_PORT=3001
```

`.env` は Git の管理外。パスワードは `.env.example` には書かない。

Git に入っていないので別に用意するもの:

- VDIF ファイル（例: `D:\vdif\wp5.1_2025277072300_5.vdif`）。リポジトリの外に置く。
- `feed_model.csv`（例: `D:\data\feed_model.csv`）。試験なら README の 2024 年版で足りる。本番は観測チームから受け取る。
- Grafana のダッシュボード。元の PC で Export as JSON し、Dashboards → New → Import で読み込む。

## 2. DB と Grafana

```powershell
# 起動（collector を起動しないよう、必ずサービス名を付ける）
docker compose -f monitor\docker-compose.yml up -d db grafana

# 状態の確認
docker compose -f monitor\docker-compose.yml ps

# 停止（データは残る）
docker compose -f monitor\docker-compose.yml stop
```

### 表の確認と作り直し

```powershell
# 表の一覧（control, pointing, weather の3つがあれば OK）
docker compose -f monitor\docker-compose.yml exec db psql -U nobeyama -d nobeyama -c "\dt"

# init.sql をもう一度流す（何度流しても安全）
Get-Content monitor\init.sql | docker compose -f monitor\docker-compose.yml exec -T db psql -U nobeyama -d nobeyama

# 古い DB で列が足りないとき
docker compose -f monitor\docker-compose.yml exec db psql -U nobeyama -d nobeyama -c "ALTER TABLE weather ADD COLUMN IF NOT EXISTS wind_direction REAL; ALTER TABLE pointing ADD COLUMN IF NOT EXISTS azimuth REAL;"
```

init.sql が自動で実行されるのは DB を最初に作ったときだけ。
`POSTGRES_PASSWORD` と `GRAFANA_PASSWORD` も最初の起動時にしか使われない。

### パスワード関係

```powershell
# DB のパスワードを .env に合わせる
docker compose -f monitor\docker-compose.yml exec db psql -U nobeyama -d nobeyama -c "ALTER USER nobeyama PASSWORD '<パスワード>';"

# PC から DB につながるか（OK と出れば成功）
uv run --extra monitor --env-file monitor/.env python -c "import os, psycopg; psycopg.connect(os.environ['MAO_DATABASE_URL']).close(); print('OK')"

# Grafana の admin パスワードをリセット
docker compose -f monitor\docker-compose.yml exec grafana grafana cli admin reset-admin-password <パスワード>
```

### Grafana のデータソース

Connections → Data sources → Add new data source → PostgreSQL

| 項目 | 値 |
| --- | --- |
| Host URL | `db:5432` |
| Database name | `nobeyama` |
| Username | `nobeyama` |
| Password | `.env` の `POSTGRES_PASSWORD`（半角で手入力。ブラウザの自動入力に注意） |
| TLS/SSL Mode | disable |
| TimescaleDB | オン |

Host は `localhost` ではなく `db`（Grafana はコンテナの中から compose のサービス名でつなぐ）。

## 3. 通し試験（ターミナル3つ）

試験をやり直すときは、先に control を空にする（同じ VDIF を流すと時刻が重なって書き込まれないため）。

```powershell
docker compose -f monitor\docker-compose.yml exec db psql -U nobeyama -d nobeyama -c "TRUNCATE control;"
```

### ターミナル1: モック COSMOS

```powershell
uv run python sandbox/mock_cosmos.py
```

### ターミナル2: VDIF の送信

```powershell
uv run mao-45m vdif send "D:\vdif\wp5.1_2025277072300_5.vdif" --port 22222 --status True
```

- `vdif send` のポートのデフォルトは 11111、制御ループの受信のデフォルトは 22222。必ず指定してそろえる。
- 研究室など共用の LAN では、末尾に `--ttl 0` を付けて PC の外に出さない（届かなければ外す）。
- 観測所のネットワークでは、本番と同じグループ・ポート（239.0.0.1:60004）に絶対に送らない。

### ターミナル3: 制御ループ（DB 書き込みあり）

```powershell
uv run --extra monitor --env-file monitor/.env mao-45m subref control --feed_model "D:\data\feed_model.csv" --feed_origin "2025-10-04T16:23:00JST" --feed_pattern "l,x,c,c,c,c,x,t,t,t,t,x,r,r,r,r,x,b,b,b,b,x,l,l,l" --control_duration "10 min" --cal_interval "2 hr" --vdif_port 22222 --integral_gain_dX 0 --integral_gain_dZ 0 --database --log_stderr_level INFO
```

- `--vdif_port 22222`: ターミナル2の `--port` とそろえる。
- `--integral_gain_dX 0 --integral_gain_dZ 0`: anti-windup の分岐が2周期目に `AttributeError: 'DataArray' object has no attribute 'time'` で落ちるバグの回避策（P 制御になる）。バグが直ったら外す。
- `--cal_interval "2 hr"`: 付けないと10秒ごとに EPL の基準がリセットされ、のこぎり波になる。
- `--feed_pattern` は必ず `"` で囲む（PowerShell がカンマを別の意味に解釈するため）。
- `--feed_origin` はこの VDIF の開始時刻にしてある。本番の値は観測チームに確認する。
- `dry_run` はデフォルトの True のまま。副鏡への指令は送らず、DB には `sent = false` で記録される。
- `Subreflector control started.` が出るまでは VDIF のフレームを待っている。出なければポートを確認する。

## 4. 結果の確認

```powershell
# control の最新5行（time が 0.5 秒ごとに進めば OK）
docker compose -f monitor\docker-compose.yml exec db psql -U nobeyama -d nobeyama -c "SELECT time, epl_c, epl_t, dx, dz, sent FROM control ORDER BY time DESC LIMIT 5"

# 3つの表の行数
docker compose -f monitor\docker-compose.yml exec db psql -U nobeyama -d nobeyama -c "SELECT (SELECT count(*) FROM weather) AS weather, (SELECT count(*) FROM pointing) AS pointing, (SELECT count(*) FROM control) AS control"

# 直近1時間の仰角の範囲（モックの仰角の確認）
docker compose -f monitor\docker-compose.yml exec db psql -U nobeyama -d nobeyama -c "SELECT min(elevation), max(elevation) FROM pointing WHERE time > now() - interval '1 hour'"
```

### Grafana のパネル用クエリ

control の時刻は VDIF の時刻（この VDIF なら 2025-10-04 16:23〜16:53 JST）。
weather と pointing の時刻はモックの「今」なので、表示範囲を分けて見る。

EPL（Unit は millimetre）:

```sql
SELECT time,
       epl_c*1e3 AS "c", epl_t*1e3 AS "t", epl_r*1e3 AS "r",
       epl_b*1e3 AS "b", epl_l*1e3 AS "l"
FROM control
WHERE $__timeFilter(time)
ORDER BY time
```

副鏡 dX, dZ（Unit は millimetre、Line interpolation は Step after）:

```sql
SELECT time,
       dx*1e3 AS "dX",
       dz*1e3 AS "dZ"
FROM control
WHERE $__timeFilter(time)
ORDER BY time
```

### 結果を見るときの注意

- モックの仰角は本物の EPL と無関係に動く（25°〜65° など）。制御ループは仰角から重力変形のぶんを差し引くので、試験中の dX, dZ の値には意味がない。
- 積分ゲイン 0 の P 制御なので、dX, dZ の動き方は本番の PI 制御とは違う。
- この VDIF（2025-10-04）は r フィードが故障していた時期のもので、r の EPL はほぼ平らになる。
- 2025-10-04 の前半に t と b の EPL が逆向きに跳ねるのは、副鏡を X 方向に動かす試験の記録と思われる（要確認）。

## 5. 調べもの用

```powershell
# VDIF ファイルの開始・終了時刻とフレーム数
uv run python sandbox/vdif_info.py "D:\vdif\wp5.1_2025277072300_5.vdif"

# VDIF が届いているかの確認（ファイルが増えれば OK。確認後は Ctrl+C で止めてファイルを消す）
uv run mao-45m vdif receive "D:\vdif\test_recv.vdif" --port 22222

# コマンドのオプション一覧
uv run mao-45m vdif send --help
uv run mao-45m subref control --help

# 文法チェック（何も出なければ OK）
uv run python -m py_compile mao_45m/subref/control.py
```

エクスプローラーのファイルサイズ表示は書き込み中に更新されないことがある。
正確には `(Get-Item D:\vdif\test_recv.vdif).Length` で確認する。

## 6. Linux（viento・シャトル PC）での読み替え

| Windows | Linux |
| --- | --- |
| `docker compose -f monitor\docker-compose.yml` | `podman-compose -f monitor/docker-compose.yml` |
| `Get-Content monitor\init.sql \| (略) exec -T db psql ...` | `podman-compose -f monitor/docker-compose.yml exec -T db psql -U nobeyama -d nobeyama < monitor/init.sql` |
| `Copy-Item` | `cp` |
| パスの `\` | `/` |
| `D:\vdif\...` | `/home/mao/data/vdif/...` など（`/home` の空き容量に注意） |

Podman 固有の注意（`,Z`、レジストリの選択、`podman rmi` でのキャッシュ対策、enable-linger）は
`monitor/README.md` の Podman の節を参照。