# -*- coding: utf-8 -*-
"""
SUUMO 新着賃貸物件 通知ツール
============================

このプログラムは、SUUMO（スーモ）の検索結果ページを定期的にチェックして、
「前回チェックしたとき」にはなかった新しい物件だけを、
エリアごとにGmailでメールしてくれるPythonプログラムです。

おおまかな流れ:
  1. エリアごとにSUUMOのページにアクセスして、物件の一覧を取り出す
  2. 前回保存しておいた一覧（data フォルダの中のファイル）と見くらべる
  3. 前回になかった物件を「新着」とする
  4. エリアごとに1通ずつ、合計3通のメールを送る
  5. 今回取り出した一覧を data フォルダに保存し直す（次回の比較に使う）

プログラミング初心者の方でも読めるように、コメント（# で始まる説明文）を
多めに書いています。コメントは実行には影響しません。
"""

# ──────────────────────────────────────────────────────────────
# ① 必要な道具（ライブラリ）を読み込む
#    import = 「他の人が作った便利な機能を持ってくる」という意味
# ──────────────────────────────────────────────────────────────
import os            # パソコンのファイルや環境変数（あとで説明）を扱う
import re            # 文字列の中から欲しい部分を取り出す（正規表現）
import json          # データをファイルに保存・読み込みするための形式
import time          # 一定時間「待つ（sleep）」ために使う
import smtplib       # メールを送るための標準機能（SMTP）
import ssl           # メール送信を暗号化（安全に）するために使う
from email.mime.text import MIMEText  # メール本文を作るための道具

import requests                       # インターネット上のページを取りに行く道具
from bs4 import BeautifulSoup         # 取ってきたHTMLから情報を取り出す道具

# python-dotenv は「ローカルPCでのテスト時」に .env ファイルを読むための道具。
# GitHub Actions 上には .env が無くても動くように、try/except で囲んでいます。
try:
    from dotenv import load_dotenv
    load_dotenv()  # 同じフォルダに .env があれば、その中身を読み込む
except Exception:
    # python-dotenv が入っていなくても、エラーで止まらないようにしておく
    pass


# ──────────────────────────────────────────────────────────────
# ② 設定（ここを変えれば動きを調整できます）
# ──────────────────────────────────────────────────────────────

# チェックしたいエリアの一覧。
#   "name"     … メールの件名などに使うエリア名
#   "filename" … そのエリアの記録を保存するファイル名（エリアごとに分ける）
#   "url"      … SUUMOの検索結果ページ（PC版・新着順）のURL
AREAS = [
    {
        "name": "錦糸町",
        "filename": "seen_kinshicho.json",
        "url": "https://suumo.jp/jj/chintai/ichiran/FR301FC001/?url=%2Fchintai%2Fichiran%2FFR301FC001%2F&ar=030&bs=040&pc=30&smk=&po1=09&po2=99&co=1&kz=1&kz=2&tc=0401303&tc=0400301&shkr1=03&shkr2=03&shkr3=03&shkr4=03&cb=7.0&ct=12.0&et=15&mb=20&mt=9999999&cn=10&ra=013&ek=004553960&rn=0045",
    },
    {
        "name": "押上",
        "filename": "seen_oshiage.json",
        "url": "https://suumo.jp/jj/chintai/ichiran/FR301FC001/?url=%2Fchintai%2Fichiran%2FFR301FC001%2F&ar=030&bs=040&pc=30&smk=&po1=09&po2=99&co=1&kz=1&kz=2&tc=0401303&tc=0400301&shkr1=03&shkr2=03&shkr3=03&shkr4=03&cb=7.0&ct=12.0&et=15&mb=20&mt=9999999&cn=10&ra=013&ek=004506820&rn=0045",
    },
    {
        "name": "住吉",
        "filename": "seen_sumiyoshi.json",
        "url": "https://suumo.jp/jj/chintai/ichiran/FR301FC001/?url=%2Fchintai%2Fichiran%2FFR301FC001%2F&ar=030&bs=040&pc=30&smk=&po1=09&po2=99&co=1&kz=1&kz=2&tc=0401303&tc=0400301&shkr1=03&shkr2=03&shkr3=03&shkr4=03&cb=7.0&ct=12.0&et=15&mb=20&mt=9999999&cn=10&ra=013&ek=004520870&rn=0045",
    },
]

# 記録ファイルを置くフォルダ（このプログラムと同じ場所の data フォルダ）
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# SUUMOへアクセスするときに名乗る「ブラウザの種類」（User-Agent）。
# これを「PCのChrome」に見せかけることで、スマホ版ではなくPC版ページが返ります。
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en;q=0.9",
}

# SUUMOに負荷をかけないための設定
WAIT_BETWEEN_AREAS = 5   # エリアごとのアクセスの間に待つ秒数（5秒）
MAX_RETRY = 2            # 取得に失敗したときに、何回までやり直すか（控えめに）
REQUEST_TIMEOUT = 20     # 1回のアクセスで、最大何秒まで待つか


# ──────────────────────────────────────────────────────────────
# ③ SUUMOのページから物件情報を取り出す関数
#    def = 「関数（ひとまとまりの処理）を定義する」という意味
# ──────────────────────────────────────────────────────────────
def fetch_html(url):
    """指定したURLのページを取りに行き、HTML（文字列）を返す。
    失敗したら少し待ってからやり直す（リトライ）。"""

    # 0回目, 1回目, ... と MAX_RETRY 回まで挑戦する
    for attempt in range(MAX_RETRY + 1):
        try:
            # requests.get でページを取得。headers でPCのふりをする。
            response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()      # エラー応答(404など)ならここで例外発生
            response.encoding = response.apparent_encoding  # 文字化け防止
            return response.text             # 成功したのでHTML本文を返す
        except Exception as e:
            # 失敗したときの処理
            print(f"  取得に失敗しました（{attempt + 1}回目）: {e}")
            if attempt < MAX_RETRY:
                time.sleep(5)                # 5秒待ってからやり直す
            else:
                raise                        # もう諦めて、エラーを上に伝える


def get_text(node):
    """HTMLの一部（node）から文字列を取り出して、前後の空白を消すだけの小さな道具。
    node が見つからなかった（None）場合は空文字を返す。"""
    if node is None:
        return ""
    return node.get_text(strip=True)


def parse_properties(html):
    """HTMLの中から物件情報を取り出して、リスト（一覧）にして返す。
    1件は辞書（dict）で、{物件名, 家賃, 間取り, 面積, URL, key} の形にする。"""

    soup = BeautifulSoup(html, "html.parser")  # HTMLを解析しやすい形に変換
    properties = []  # ここに見つけた物件をためていく

    # SUUMOの一覧ページでは、建物1つが "cassetteitem" というまとまりになっている。
    buildings = soup.select("div.cassetteitem")

    for building in buildings:
        # 建物名（マンション名など）を取り出す
        name = get_text(building.select_one(".cassetteitem_content-title"))

        # --- ここから建物ごとの共通情報（所在地・駅徒歩・築年数など） ---

        # 所在地（例: 東京都江東区亀戸３）
        address = get_text(building.select_one(".cassetteitem_detail-col1"))

        # 駅徒歩（例: JR総武線/亀戸駅 歩14分）。複数の駅が並ぶことがあるので
        # それぞれの行を取り出し、" / " でつなぐ。
        access_node = building.select_one(".cassetteitem_detail-col2")
        if access_node is not None:
            station_lines = [
                t.get_text(strip=True)
                for t in access_node.select(".cassetteitem_detail-text")
            ]
            station_lines = [t for t in station_lines if t]  # 空を除く
            access = " / ".join(station_lines) if station_lines \
                else access_node.get_text(" ", strip=True)
        else:
            access = ""

        # 3列目には「築年数」と「建物の階数」が入っている（例: 築9年 / 3階建）
        col3 = building.select(".cassetteitem_detail-col3 div")
        chiku = get_text(col3[0]) if len(col3) >= 1 else ""            # 築年数
        building_floors = get_text(col3[1]) if len(col3) >= 2 else ""  # 建物の階数

        # 1つの建物に複数の部屋（家賃ちがい）が並んでいることがある。
        # 各部屋は表（table）の中の行（tr）になっている。
        rooms = building.select("table.cassetteitem_other tbody tr")

        for room in rooms:
            # 家賃（例: 9.5万円）
            rent = get_text(room.select_one(".cassetteitem_other-emphasis")) \
                or get_text(room.select_one(".cassetteitem_price--rent"))

            # 間取り（例: 1LDK）
            madori = get_text(room.select_one(".cassetteitem_madori"))

            # 専有面積（例: 30.5m2）
            menseki = get_text(room.select_one(".cassetteitem_menseki"))

            # 階（その部屋の所在階。例: 1階 / 2-3階 / B1階）。
            # 部屋の各セル（td）の中から「◯階」とだけ書かれたセルを探す。
            floor = ""
            for td in room.select("td"):
                t = td.get_text(strip=True)
                # 「階」で終わり、数字やB・ハイフンだけで構成されるセルが所在階
                if t and re.fullmatch(r"[B0-9\-－ー\s]*階", t):
                    floor = t
                    break

            # その部屋の詳細ページへのリンクを探す
            link = room.select_one("a[href*='/chintai/jnc_']") \
                or room.select_one("td a[href]")

            if link is None:
                # リンクが無い行（見出しなど）は飛ばす
                continue

            href = link.get("href", "")
            # SUUMOのリンクは "/chintai/jnc_..." のように先頭が省略されているので、
            # 先頭に "https://suumo.jp" を付けて完全なURLにする。
            if href.startswith("/"):
                full_url = "https://suumo.jp" + href
            else:
                full_url = href

            # 物件を一意に見分けるための「キー」を作る。
            # 詳細ページのURLに含まれる "jnc_数字" の部分を使う（重複を防げる）。
            m = re.search(r"jnc_\d+", href)
            key = m.group(0) if m else full_url

            # 1件分の情報を辞書にまとめてリストに追加
            properties.append({
                "key": key,                       # 新着判定に使う識別子
                "name": name,                     # 物件名
                "rent": rent,                     # 家賃
                "madori": madori,                 # 間取り
                "menseki": menseki,               # 面積
                "floor": floor,                   # 階（所在階）
                "building_floors": building_floors,  # 建物の階数（例: 3階建）
                "chiku": chiku,                   # 築年数（例: 築9年）
                "access": access,                 # 駅徒歩
                "address": address,               # 所在地
                "url": full_url,                  # 物件ページのURL
            })

    return properties


# ──────────────────────────────────────────────────────────────
# ④ 前回の記録を読み書きする関数
# ──────────────────────────────────────────────────────────────
def load_seen_keys(filename):
    """前回保存した「見たことのある物件キー」の一覧を読み込む。
    まだファイルが無ければ None を返す（＝初回実行のしるし）。"""
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return None  # ファイルが無い＝今回が初めての実行
    with open(path, "r", encoding="utf-8") as f:
        return set(json.load(f))  # set（集合）にして比較しやすくする


def save_seen_keys(filename, keys):
    """今回の物件キー一覧をファイルに保存する（次回の比較に使う）。"""
    os.makedirs(DATA_DIR, exist_ok=True)  # data フォルダが無ければ作る
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        # 並び順を安定させるため sorted（並べ替え）してから保存
        json.dump(sorted(keys), f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────────────────────
# ⑤ メールを送る関数
# ──────────────────────────────────────────────────────────────
def send_email(subject, body):
    """Gmailのアプリパスワードを使って、自分から自分宛にメールを送る。"""

    # 認証情報は「環境変数」から読み込む（コードに直接書かない）。
    # ・ローカルPC … .env ファイルから読まれる
    # ・GitHub上   … GitHub Secrets から読まれる
    gmail_address = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")

    # 設定が無ければ送れないので、分かりやすいエラーを出して止める
    if not gmail_address or not app_password:
        raise RuntimeError(
            "GMAIL_ADDRESS と GMAIL_APP_PASSWORD が設定されていません。"
            "ローカルなら .env ファイル、GitHubなら Secrets を確認してください。"
        )

    # メール本文オブジェクトを作る（日本語が文字化けしないよう utf-8 を指定）
    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = gmail_address
    message["To"] = gmail_address  # 自分宛に送る

    # GmailのSMTPサーバー（smtp.gmail.com）にSSLで安全に接続して送信
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(gmail_address, app_password)  # アプリパスワードでログイン
        server.send_message(message)               # 送信
    print(f"  メール送信しました: {subject}")


def format_items(lines, items):
    """物件のリストを受け取り、1件ずつ概要（物件名・家賃・間取り・面積・URL）を
    lines（文章の行リスト）に追加する小さな道具。"""
    for i, item in enumerate(items, start=1):
        # 階の表示。建物の階数も分かれば「1階 / 3階建」のように添える。
        if item.get("building_floors"):
            floor_text = f"{item['floor']} / {item['building_floors']}"
        else:
            floor_text = item["floor"]

        lines.append(f"■ {i}. {item['name']}")
        lines.append(f"   家賃　: {item['rent']}")
        lines.append(f"   間取り: {item['madori']}")
        lines.append(f"   面積　: {item['menseki']}")
        lines.append(f"   階　　: {floor_text}")
        lines.append(f"   築年数: {item.get('chiku', '')}")
        lines.append(f"   駅徒歩: {item.get('access', '')}")
        lines.append(f"   所在地: {item.get('address', '')}")
        lines.append(f"   URL　 : {item['url']}")
        lines.append("")  # 物件のあいだに空行


def build_body(area_name, new_items, is_first_run, all_items):
    """メール本文の文章を組み立てて返す。
    new_items … 新着物件のリスト / all_items … 今回取得した全物件のリスト。"""

    lines = []  # 1行ずつためていって、最後に改行でつなぐ

    if is_first_run:
        # 初回は「新着」とは扱わないが、今登録した物件の概要は一覧で載せる
        lines.append(f"【{area_name}】初回登録のお知らせ")
        lines.append("")
        lines.append(
            f"今回が初回の実行です。現在の {len(all_items)} 件を記録として保存しました。"
        )
        lines.append("次回からは、新しく追加された物件だけをお知らせします。")
        lines.append("")
        lines.append("── 現在登録されている物件 ──")
        lines.append("")
        format_items(lines, all_items)  # 全件の概要を載せる
        return "\n".join(lines)

    if not new_items:
        # 新着が0件のとき
        lines.append(f"【{area_name}】新着物件はありませんでした。")
        return "\n".join(lines)

    # 新着が1件以上あるとき：1件ずつ詳しく載せる
    lines.append(f"【{area_name}】新着物件が {len(new_items)} 件あります。")
    lines.append("")
    format_items(lines, new_items)  # 新着の概要を載せる

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# ⑥ メインの処理（プログラムの本体）
# ──────────────────────────────────────────────────────────────
def process_area(area):
    """1エリア分の処理：取得 → 比較 → メール送信 → 記録の保存。"""

    name = area["name"]
    print(f"[{name}] 物件を取得中 ...")

    # (1) ページを取得して物件一覧を取り出す
    html = fetch_html(area["url"])
    items = parse_properties(html)
    print(f"[{name}] {len(items)} 件の物件が見つかりました。")

    # (2) 前回の記録を読み込む
    previous_keys = load_seen_keys(area["filename"])
    is_first_run = previous_keys is None  # ファイルが無ければ初回

    # (3) 新着（前回になかった物件）を見つける
    if is_first_run:
        new_items = []  # 初回は新着としては扱わない
    else:
        new_items = [it for it in items if it["key"] not in previous_keys]

    # (4) メールの件名を作る
    if is_first_run:
        subject = f"【{name}】初回登録（{len(items)}件を記録）"
    elif new_items:
        subject = f"【{name}】新着{len(new_items)}件"
    else:
        subject = f"【{name}】新着なし"

    # (5) 本文を作って送信（毎回かならず1通送る）
    body = build_body(name, new_items, is_first_run, items)
    send_email(subject, body)

    # (6) 今回の一覧を保存（次回の比較用）
    current_keys = [it["key"] for it in items]
    save_seen_keys(area["filename"], current_keys)
    print(f"[{name}] 記録を保存しました。")


def main():
    """全エリアを順番に処理する。エリアの間は数秒待つ。"""
    print("=== SUUMO 新着通知ツール 開始 ===")

    for index, area in enumerate(AREAS):
        try:
            process_area(area)
        except Exception as e:
            # あるエリアで失敗しても、他のエリアは続けられるようにする
            print(f"[{area['name']}] 処理中にエラーが発生しました: {e}")

        # 最後のエリア以外は、SUUMOに優しくするため少し待つ
        if index < len(AREAS) - 1:
            print(f"  {WAIT_BETWEEN_AREAS} 秒待機します ...")
            time.sleep(WAIT_BETWEEN_AREAS)

    print("=== すべて完了しました ===")


# このファイルが「直接実行されたとき」だけ main() を動かす、というお決まりの書き方
if __name__ == "__main__":
    main()
