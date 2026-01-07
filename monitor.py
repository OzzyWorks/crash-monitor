#!/usr/bin/env python3
"""
米国株式市場暴落監視ツール

思想:
- 予測しない: 将来を予測せず、現在の事実のみを報告する
- 感情を入れない: 機械的な判定基準のみで判断する
- 判断を二択に絞る: 「投入検討」か「投入対象外」のみ
- 暴落という瞬間を逃さない: 初回検知を最重要視する
"""

import json
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
import yfinance as yf
import requests


# 監視対象シンボル
SYMBOLS = {
    'nasdaq': '^NDX',   # NASDAQ100
    'sp500': '^GSPC',   # S&P500
    'vix': '^VIX'       # VIX指数
}

# 判定基準
CRASH_THRESHOLD_MAJOR = -20.0  # NASDAQ100が52週高値比で-20%以下
CRASH_THRESHOLD_MINOR = -15.0  # NASDAQ100が-15%以下
VIX_THRESHOLD = 30.0           # VIX指数が30以上

# 52週間の営業日数（約252日）
LOOKBACK_DAYS = 252

# 状態ファイルパス
STATE_FILE = 'state.json'


def get_market_data() -> Dict[str, Dict[str, float]]:
    """
    Yahoo Financeから市場データを取得する
    
    Returns:
        各指数の現在値、52週高値、下落率を含む辞書
    """
    result = {}
    
    # 過去1年分のデータ取得期間を計算（営業日を考慮して余裕を持たせる）
    end_date = datetime.now()
    start_date = end_date - timedelta(days=LOOKBACK_DAYS + 100)
    
    for name, symbol in SYMBOLS.items():
        try:
            ticker = yf.Ticker(symbol)
            hist = ticker.history(start=start_date, end=end_date)
            
            if hist.empty:
                print(f"警告: {symbol} のデータが取得できませんでした", file=sys.stderr)
                continue
            
            current_price = hist['Close'].iloc[-1]
            
            if name == 'vix':
                # VIXは下落率を計算しない
                result[name] = {
                    'symbol': symbol,
                    'current': round(current_price, 2),
                    'value': round(current_price, 2)
                }
            else:
                # 過去252営業日の高値を取得
                if len(hist) >= LOOKBACK_DAYS:
                    high_52w = hist['High'].iloc[-LOOKBACK_DAYS:].max()
                else:
                    # データが不足している場合は取得可能な範囲の高値
                    high_52w = hist['High'].max()
                
                # 下落率を計算（負の値）
                drawdown = ((current_price - high_52w) / high_52w) * 100
                
                result[name] = {
                    'symbol': symbol,
                    'current': round(current_price, 2),
                    'high_52w': round(high_52w, 2),
                    'drawdown': round(drawdown, 2)
                }
                
        except Exception as e:
            print(f"エラー: {symbol} のデータ取得中にエラーが発生しました: {e}", file=sys.stderr)
            continue
    
    return result


def check_crash_condition(data: Dict[str, Dict[str, float]]) -> Tuple[bool, Optional[str]]:
    """
    暴落条件を判定する
    
    Args:
        data: 市場データ
        
    Returns:
        (暴落判定結果, トリガー理由)
    """
    if 'nasdaq' not in data or 'vix' not in data:
        print("エラー: 必要なデータが不足しています", file=sys.stderr)
        return False, None
    
    nasdaq_drawdown = data['nasdaq']['drawdown']
    vix_value = data['vix']['value']
    
    # 条件1: NASDAQ100が52週高値比-20%以下
    if nasdaq_drawdown <= CRASH_THRESHOLD_MAJOR:
        trigger = f"NASDAQ100 が 52週高値比 {CRASH_THRESHOLD_MAJOR}% を超える下落に突入しました。"
        return True, trigger
    
    # 条件2: NASDAQ100が-15%以下 かつ VIXが30以上
    if nasdaq_drawdown <= CRASH_THRESHOLD_MINOR and vix_value >= VIX_THRESHOLD:
        trigger = f"NASDAQ100 が {CRASH_THRESHOLD_MINOR}% 下落、かつ VIX指数が {VIX_THRESHOLD} を超えました。"
        return True, trigger
    
    return False, None


def load_state() -> Dict:
    """
    前回の状態を読み込む
    
    Returns:
        状態辞書
    """
    if not os.path.exists(STATE_FILE):
        return {
            'is_crash': False,
            'first_detected': None,
            'last_checked': None
        }
    
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"警告: 状態ファイルの読み込みに失敗しました: {e}", file=sys.stderr)
        return {
            'is_crash': False,
            'first_detected': None,
            'last_checked': None
        }


def save_state(state: Dict):
    """
    現在の状態を保存する
    
    Args:
        state: 保存する状態辞書
    """
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"エラー: 状態ファイルの保存に失敗しました: {e}", file=sys.stderr)


def send_slack_notification(message: str, webhook_url: str):
    """
    Slackに通知を送信する
    
    Args:
        message: 送信するメッセージ
        webhook_url: Slack Webhook URL
    """
    try:
        payload = {
            'text': message,
            'unfurl_links': False,
            'unfurl_media': False
        }
        
        response = requests.post(
            webhook_url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=10
        )
        
        if response.status_code == 200:
            print("Slack通知を送信しました")
        else:
            print(f"警告: Slack通知の送信に失敗しました (Status: {response.status_code})", file=sys.stderr)
            
    except Exception as e:
        print(f"エラー: Slack通知の送信中にエラーが発生しました: {e}", file=sys.stderr)


def format_initial_alert(data: Dict[str, Dict[str, float]], trigger: str) -> str:
    """
    初回検知時の通知メッセージをフォーマットする
    
    Args:
        data: 市場データ
        trigger: トリガー理由
        
    Returns:
        フォーマット済みメッセージ
    """
    nasdaq = data.get('nasdaq', {})
    sp500 = data.get('sp500', {})
    vix = data.get('vix', {})
    
    message = f"""【米国株式市場・暴落監視レポート】

■ 市場状態
💥 投入検討

■ 初回検知トリガー
{trigger}

■ 市場データ
NASDAQ100: {nasdaq.get('current', 'N/A')} ({nasdaq.get('drawdown', 'N/A')}%)
S&P500: {sp500.get('current', 'N/A')} ({sp500.get('drawdown', 'N/A')}%)
VIX指数: {vix.get('value', 'N/A')}

■ 補足
価格下落と市場心理の悪化が同時に発生しています。"""
    
    return message


def format_continuation_alert(data: Dict[str, Dict[str, float]]) -> str:
    """
    継続中の通知メッセージをフォーマットする
    
    Args:
        data: 市場データ
        
    Returns:
        フォーマット済みメッセージ
    """
    nasdaq = data.get('nasdaq', {})
    sp500 = data.get('sp500', {})
    vix = data.get('vix', {})
    
    message = f"""【米国株式市場・暴落監視レポート】

■ 市場状態
⚠️ 投入検討（継続中）

NASDAQ100 {nasdaq.get('drawdown', 'N/A')}% / S&P500 {sp500.get('drawdown', 'N/A')}% / VIX {vix.get('value', 'N/A')}"""
    
    return message


def main():
    """
    メイン処理
    """
    print(f"実行開始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Slack Webhook URLを環境変数から取得
    webhook_url = os.environ.get('SLACK_WEBHOOK_URL')
    if not webhook_url:
        print("エラー: SLACK_WEBHOOK_URL 環境変数が設定されていません", file=sys.stderr)
        sys.exit(1)
    
    # 市場データを取得
    print("市場データを取得中...")
    data = get_market_data()
    
    if not data:
        print("エラー: 市場データの取得に失敗しました", file=sys.stderr)
        sys.exit(1)
    
    # データを表示
    print("\n取得データ:")
    for name, values in data.items():
        print(f"  {name}: {values}")
    
    # 暴落条件を判定
    is_crash, trigger = check_crash_condition(data)
    
    # 前回の状態を読み込む
    prev_state = load_state()
    prev_is_crash = prev_state.get('is_crash', False)
    
    current_time = datetime.now().isoformat()
    
    # 状態判定と通知
    if is_crash:
        if not prev_is_crash:
            # 初回検知
            print("\n🚨 暴落を初回検知しました")
            message = format_initial_alert(data, trigger)
            send_slack_notification(message, webhook_url)
            
            # 状態を保存
            new_state = {
                'is_crash': True,
                'first_detected': current_time,
                'last_checked': current_time
            }
            save_state(new_state)
            
        else:
            # 継続中
            print("\n⚠️ 暴落継続中")
            message = format_continuation_alert(data)
            send_slack_notification(message, webhook_url)
            
            # 状態を更新
            new_state = prev_state.copy()
            new_state['last_checked'] = current_time
            save_state(new_state)
            
    else:
        # 投入対象外
        print("\n✅ 投入対象外（通常状態）")
        
        if prev_is_crash:
            # 暴落状態から回復
            print("   暴落状態から回復しました")
        
        # 状態を保存
        new_state = {
            'is_crash': False,
            'first_detected': None,
            'last_checked': current_time
        }
        save_state(new_state)
    
    print(f"\n実行完了: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == '__main__':
    main()
