import os
import json
import re
import random
import logging
from pathlib import Path
from datetime import datetime, timedelta
from threading import Lock
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    InlineQueryResultArticle,
    InputTextMessageContent
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    CallbackContext,
    InlineQueryHandler,
    JobQueue
)

# ===== 配置区 =====
TOKEN = "YOUR_BOT_TOKEN"  # ⚠️替换为你的机器人Token
ADMIN_ID = 7606364039     # ⚠️替换为你的管理员ID
DATA_FILE = Path("user_data.json")
TRON_ADDRESS = "TP8bZPJY2KUAZwM6bLGmKfv3MHmfSt9jUX"
RED_PACKET_MIN_AMOUNT = 10  # 红包最低金额
RED_PACKET_MAX_COUNT = 100  # 红包最大个数
# =================

# ===== 日志配置 =====
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== 线程安全文件锁 =====
FILE_LOCK = Lock()

# ===== 数据管理 =====
def load_user_data():
    default_data = {
        "balance": {},
        "logs": [],
        "bets": {},
        "pending_rolls": {},
        "history": [],
        "in_progress": {},
        "red_packets": {},
        "user_red_packets": {}
    }
    with FILE_LOCK:
        if DATA_FILE.exists():
            try:
                with open(DATA_FILE, "r", encoding='utf-8') as f:
                    data = json.load(f)
                for key in default_data.keys():
                    if key not in data:
                        data[key] = default_data[key]
                return data
            except Exception as e:
                logger.error(f"加载数据失败: {str(e)}")
                return default_data
        return default_data

def save_user_data(data):
    with FILE_LOCK:
        try:
            with open(DATA_FILE, "w", encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存数据失败: {str(e)}")

def add_log(action, user_id=None, amount=None, target_user=None):
    data = load_user_data()
    log_entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "admin": user_id,
        "target": target_user,
        "amount": amount
    }
    data["logs"].append(log_entry)
    save_user_data(data)

# ===== 赔率配置 =====
ODDS = {
    '大': 2, '小': 2, '单': 2, '双': 2,
    '大单': 3.4, '小单': 4.4, '大双': 4.4, '小双': 3.4,
    '豹子': 32, '顺子': 8, '对子': 2.1,
    '豹1': 200, '豹2': 200, '豹3': 200, '豹4': 200, 
    '豹5': 200, '豹6': 200,
    '定位胆4': 58, '定位胆5': 28, '定位胆6': 16, '定位胆7': 12,
    '定位胆8': 8, '定位胆9': 7, '定位胆10': 7, '定位胆11': 6,
    '定位胆12': 6, '定位胆13': 8, '定位胆14': 12, '定位胆15': 16,
    '定位胆16': 28, '定位胆17': 58
}

# ===== 红包配置 =====
RED_PACKET_STATES = {
    "CREATING": 0,
    "SET_AMOUNT": 1,
    "SET_COUNT": 2,
    "CONFIRMING": 3
}

class RedPacketHandler:
    @staticmethod
    def generate_id():
        return datetime.now().strftime("%Y%m%d%H%M%S%f")

    @staticmethod
    def calculate_amounts(total_amount, count):
        """使用二倍均值法分配红包"""
        amounts = []
        remaining = total_amount
        
        for _ in range(count - 1):
            max_amount = remaining / (count - len(amounts)) * 2
            amount = round(random.uniform(0.01, max_amount), 2)
            amounts.append(amount)
            remaining -= amount
        
        amounts.append(round(remaining, 2))
        random.shuffle(amounts)
        return amounts

# ===== 核心游戏功能 =====
def parse_bet(message: str):
    bet_details = {}
    message = message.lower().replace(' ', '')
    
    patterns = [
        (r'(大单|dd)(\d+)', '大单'),
        (r'(大双|ds)(\d+)', '大双'),
        (r'(小单|xd)(\d+)', '小单'),
        (r'(小双|xs)(\d+)', '小双'),
        (r'(大|da)(\d+)', '大'),
        (r'(小|x)(\d+)', '小'),
        (r'(单|dan)(\d+)', '单'),
        (r'(双|s)(\d+)', '双'),
        (r'(bz|豹子)(1|2|3|4|5|6)(\d+)', lambda m: f'豹{m.group(2)}'),
        (r'(豹子|bz)(\d+)', '豹子'),
        (r'(顺子|sz)(\d+)', '顺子'),
        (r'(对子|dz)(\d+)', '对子'),
        (r'(定位胆|dwd)(4|5|6|7|8|9|10|11|12|13|14|15|16|17)(\d+)', lambda m: f'定位胆{m.group(2)}'),
        (r'(4|5|6|7|8|9|10|11|12|13|14|15|16|17)y(\d+)', lambda m: f'定位胆{m.group(1)}'),
        (r'(\d+)(大|小|单|双)', lambda m: f"{m.group(2)}{m.group(1)}"),
    ]

    for pattern, key in patterns:
        for match in re.finditer(pattern, message):
            try:
                if callable(key):
                    bet_type = key(match)
                    amount_str = match.group(3) if 'y' not in match.group(0) else match.group(2)
                else:
                    bet_type = key
                    amount_str = match.group(2)
                
                amount = int(amount_str)
                bet_details[bet_type] = bet_details.get(bet_type, 0) + amount
                message = message.replace(match.group(0), '', 1)
            except Exception as e:
                logger.warning(f"解析下注失败: {str(e)}")
                continue

    return bet_details if bet_details else None

def calculate_result(dice_values):
    total = sum(dice_values)
    return {
        'values': dice_values,
        'total': total,
        'is_big': total > 10,
        'is_small': total <= 10,
        'is_odd': total % 2 != 0,
        'is_even': total % 2 == 0,
        'is_triple': len(set(dice_values)) == 1,
        'is_straight': sorted(dice_values) in [[1,2,3], [2,3,4], [3,4,5], [4,5,6]],
        'is_pair': len(set(dice_values)) == 2,
        'triple_num': dice_values[0] if len(set(dice_values)) == 1 else None
    }

def calculate_winnings(bet_details, result):
    winnings = 0
    winning_bets = []
    
    if result['is_triple']:
        for bet_type, amount in bet_details.items():
            if bet_type == '豹子':
                winnings += amount * ODDS[bet_type]
                winning_bets.append(bet_type)
            elif bet_type.startswith('豹') and len(bet_type) > 1:
                try:
                    num = int(bet_type[1:])
                    if num == result['triple_num']:
                        winnings += amount * ODDS[bet_type]
                        winning_bets.append(bet_type)
                except ValueError:
                    continue
    
    for bet_type, amount in bet_details.items():
        if bet_type in winning_bets:
            continue
        
        win = False
        if bet_type == '大' and result['is_big']:
            win = True
        elif bet_type == '小' and result['is_small']:
            win = True
        elif bet_type == '单' and result['is_odd']:
            win = True
        elif bet_type == '双' and result['is_even']:
            win = True
        elif bet_type == '大单' and result['is_big'] and result['is_odd']:
            win = True
        elif bet_type == '小单' and result['is_small'] and result['is_odd']:
            win = True
        elif bet_type == '大双' and result['is_big'] and result['is_even']:
            win = True
        elif bet_type == '小双' and result['is_small'] and result['is_even']:
            win = True
        elif bet_type == '顺子' and result['is_straight']:
            win = True
        elif bet_type == '对子' and result['is_pair']:
            win = True
        elif bet_type.startswith('定位胆'):
            try:
                target = int(bet_type[3:])
                if result['total'] == target:
                    win = True
            except ValueError:
                continue
        
        if win:
            winnings += amount * ODDS.get(bet_type, 0)
            winning_bets.append(bet_type)
    
    return winnings, winning_bets

# ===== 管理员指令 =====
async def admin_add(update: Update, context: CallbackContext):
    try:
        if update.message.from_user.id != ADMIN_ID:
            await update.message.reply_text("❌ 权限不足")
            return
        
        target_user = int(context.args[0])
        amount = int(context.args[1])
        data = load_user_data()
        current = data['balance'].get(str(target_user), 0)
        data['balance'][str(target_user)] = current + amount
        save_user_data(data)
        add_log("ADD_BALANCE", update.message.from_user.id, amount, target_user)
        await update.message.reply_text(
            f"✅ 充值成功\n━━━━━━━━━━━━\n"
            f"用户ID: {target_user}\n"
            f"充值金额: +{amount} USDT\n"
            f"当前余额: {current + amount} USDT\n"
            f"操作员: {update.message.from_user.id}"
        )
    except Exception as e:
        logger.error(f"管理员充值失败: {str(e)}")
        await update.message.reply_text("⚠️ 格式错误\n使用: /add 用户ID 金额")

async def admin_set(update: Update, context: CallbackContext):
    try:
        if update.message.from_user.id != ADMIN_ID:
            await update.message.reply_text("❌ 权限不足")
            return
        
        target_user = int(context.args[0])
        amount = int(context.args[1])
        data = load_user_data()
        old_balance = data['balance'].get(str(target_user), 0)
        data['balance'][str(target_user)] = amount
        save_user_data(data)
        add_log("SET_BALANCE", update.message.from_user.id, amount, target_user)
        await update.message.reply_text(
            f"✅ 余额设置成功\n━━━━━━━━━━━━\n"
            f"用户ID: {target_user}\n"
            f"原余额: {old_balance} USDT\n"
            f"新余额: {amount} USDT\n"
            f"操作员: {update.message.from_user.id}"
        )
    except Exception as e:
        logger.error(f"设置余额失败: {str(e)}")
        await update.message.reply_text("⚠️ 格式错误\n使用: /set 用户ID 金额")

async def admin_list(update: Update, context: CallbackContext):
    try:
        if update.message.from_user.id != ADMIN_ID:
            await update.message.reply_text("❌ 权限不足")
            return
        
        data = load_user_data()
        if not data['balance']:
            await update.message.reply_text("暂无用户数据")
            return
        
        msg = ["📊 用户余额\n━━━━━━━━━━━━"]
        for uid, bal in data['balance'].items():
            msg.append(f"ID: {uid} | 余额: {bal} USDT")
        await update.message.reply_text("\n".join(msg[:20]))
    except Exception as e:
        logger.error(f"查询用户列表失败: {str(e)}")

async def admin_logs(update: Update, context: CallbackContext):
    try:
        if update.message.from_user.id != ADMIN_ID:
            await update.message.reply_text("❌ 权限不足")
            return
        
        data = load_user_data()
        if not data['logs']:
            await update.message.reply_text("暂无日志")
            return
        
        msg = ["📜 操作日志(最近10条)\n━━━━━━━━━━━━"]
        for log in data['logs'][-10:]:
            msg.append(
                f"时间: {log['time']}\n"
                f"操作: {log['action']}\n"
                f"目标: {log['target']}\n"
                f"金额: {log['amount']} USDT\n"
                f"━━━━━━━━━━━━"
            )
        await update.message.reply_text("\n".join(msg))
    except Exception as e:
        logger.error(f"查询日志失败: {str(e)}")

async def admin_invite(update: Update, context: CallbackContext):
    try:
        if update.message.from_user.id != ADMIN_ID:
            await update.message.reply_text("❌ 权限不足")
            return
        
        chat_id = update.message.chat.id
        invite_link = await context.bot.create_chat_invite_link(
            chat_id, 
            member_limit=1,
            creates_join_request=True
        )
        await update.message.reply_text(
            f"📩 邀请链接:\n{invite_link.invite_link}\n\n"
            "• 有效期：永久\n"
            "• 使用次数：无限制"
        )
    except Exception as e:
        logger.error(f"生成邀请链接失败: {str(e)}")
        await update.message.reply_text(f"⚠️ 生成链接失败: {str(e)}")

async def handle_admin_commands(update: Update, context: CallbackContext):
    try:
        if update.message.from_user.id != ADMIN_ID:
            return
        if update.message.chat.type not in ["group", "supergroup"]:
            return
        if not update.message.reply_to_message:
            return
        
        target_user = update.message.reply_to_message.from_user.id
        command = update.message.text.strip()
        
        if not re.fullmatch(r'^[+-]\d+$', command):
            return
        
        amount = int(command)
        data = load_user_data()
        current = data['balance'].get(str(target_user), 0)
        
        if amount > 0:
            action_type = "ADD_BALANCE"
            data['balance'][str(target_user)] = current + abs(amount)
        else:
            action_type = "SUB_BALANCE"
            if current < abs(amount):
                await update.message.reply_text(f"❌ 余额不足 | 用户ID: {target_user}")
                return
            data['balance'][str(target_user)] = current - abs(amount)
        
        save_user_data(data)
        add_log(action_type, ADMIN_ID, abs(amount), target_user)
        
        await update.message.reply_text(
            f"✅ 操作成功\n━━━━━━━━━━━━\n"
            f"用户ID: {target_user}\n"
            f"变动金额: {command} USDT\n"
            f"当前余额: {data['balance'][str(target_user)]} USDT"
        )
    except Exception as e:
        logger.error(f"管理员快捷操作失败: {str(e)}")

# ===== 用户功能 =====
async def start(update: Update, context: CallbackContext) -> None:
    try:
        user_id = update.message.from_user.id
        data = load_user_data()
        
        if str(user_id) not in data['balance']:
            data['balance'][str(user_id)] = 0
            save_user_data(data)
        
        keyboard = [
            [InlineKeyboardButton("💰 充值", callback_data='deposit'),
             InlineKeyboardButton("💸 提现", callback_data='withdraw')],
            [InlineKeyboardButton("💳 余额", callback_data='check_balance'),
             InlineKeyboardButton("🧧 发红包", callback_data='send_red_packet')],
            [InlineKeyboardButton("📦 我的红包", callback_data='my_packets'),
             InlineKeyboardButton("📖 帮助", callback_data='help')]
        ]
        
        await update.message.reply_text(
            f"🎲 骰子游戏系统\n━━━━━━━━━━━━\n"
            f"ID: {user_id}\n"
            f"余额: {data['balance'][str(user_id)]} USDT\n"
            f"━━━━━━━━━━━━\n"
            f"✅ TRC20自动充值\n"
            f"✅ 官方骰子公平公正",
            reply_markup=InlineKeyboardMarkup(keyboard)
    except Exception as e:
        logger.error(f"启动命令失败: {str(e)}")

async def place_bet(update: Update, context: CallbackContext) -> None:
    try:
        if update.message.chat.type != "private":
            return
        
        user_id = update.message.from_user.id
        data = load_user_data()
        
        if data['in_progress'].get(str(user_id), False):
            await update.message.reply_text("⏳ 请先完成当前对局")
            return
        
        bet_details = parse_bet(update.message.text)
        if not bet_details:
            await update.message.reply_text("⚠️ 下注格式错误\n示例：大单100 豹子50 定位胆4 10")
            return
        
        total_bet = sum(bet_details.values())
        balance = data['balance'].get(str(user_id), 0)
        
        if balance < total_bet:
            await update.message.reply_text(f"❌ 余额不足\n当前余额: {balance} USDT\n需: {total_bet} USDT")
            return
        
        data['in_progress'][str(user_id)] = True
        data['balance'][str(user_id)] = balance - total_bet
        data['bets'][str(user_id)] = bet_details
        save_user_data(data)
        
        bet_list = "\n".join([f"• {k}: {v} USDT" for k, v in bet_details.items()])
        
        await update.message.reply_text(
            f"🎯 下注成功\n━━━━━━━━━━━━\n"
            f"下注内容:\n{bet_list}\n"
            f"总下注: {total_bet} USDT\n"
            f"剩余: {data['balance'][str(user_id)]} USDT\n"
            f"请选择开奖方式:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎰 机摇骰子", callback_data='roll_machine')],
                [InlineKeyboardButton("👋 手摇骰子", callback_data='roll_user')]
            ]))
    except Exception as e:
        logger.error(f"下注失败: {str(e)}")
        await update.message.reply_text("⚠️ 下注处理出错，请稍后再试")

async def handle_dice_result(update: Update, context: CallbackContext):
    try:
        if not update.message or not update.message.dice:
            return
        
        user_id = update.message.from_user.id
        data = load_user_data()
        
        if str(user_id) not in data['pending_rolls']:
            return
        
        if len(data['pending_rolls'][str(user_id)]) < 2:
            data['pending_rolls'][str(user_id)].append(update.message.dice.value)
            save_user_data(data)
            return
        
        dice_values = data['pending_rolls'][str(user_id)][:2] + [update.message.dice.value]
        del data['pending_rolls'][str(user_id)]
        save_user_data(data)
        
        result = calculate_result(dice_values)
        bet_details = data['bets'].get(str(user_id), {})
        
        await show_result(user_id, result, bet_details, data, context, is_machine=False)
        data['in_progress'][str(user_id)] = False
        save_user_data(data)
    except Exception as e:
        logger.error(f"处理骰子结果失败: {str(e)}")

# ===== 红包功能 =====
async def handle_red_packet_creation(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'send_red_packet':
        logger.debug("开始创建红包流程")
        context.user_data['red_packet'] = {
            'state': RED_PACKET_STATES["SET_AMOUNT"],
            'id': None,
            'amount': 0.0,
            'count': 0
        }
        try:
            await query.edit_message_text(
                "🎁 创建红包\n━━━━━━━━━━━━\n"
                f"请输入总金额（最低{RED_PACKET_MIN_AMOUNT} USDT）",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ 取消", callback_data='cancel_red_packet')]
                ])
            )
        except Exception as e:
            logger.error(f"消息发送失败: {str(e)}")
    
    elif query.data == 'cancel_red_packet':
        context.user_data.pop('red_packet', None)
        await query.edit_message_text("❌ 已取消红包创建")

async def handle_red_packet_input(update: Update, context: CallbackContext):
    if 'red_packet' not in context.user_data:
        return
    
    user_id = str(update.message.from_user.id)
    data = load_user_data()
    state = context.user_data['red_packet']['state']
    
    try:
        if state == RED_PACKET_STATES["SET_AMOUNT"]:
            try:
                amount = float(update.message.text.strip().replace('USDT', ''))
            except ValueError:
                await update.message.reply_text("⚠️ 请输入有效的数字金额")
                return
            
            if amount < RED_PACKET_MIN_AMOUNT:
                await update.message.reply_text(f"❌ 金额不能低于{RED_PACKET_MIN_AMOUNT} USDT")
                return
            
            if data['balance'].get(user_id, 0) < amount:
                await update.message.reply_text("❌ 余额不足")
                return
            
            context.user_data['red_packet'].update({
                'amount': amount,
                'state': RED_PACKET_STATES["SET_COUNT"]
            })
            
            await update.message.reply_text(
                f"✅ 已设置金额: {amount} USDT\n"
                f"请输入红包个数（1-{RED_PACKET_MAX_COUNT}）",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ 取消", callback_data='cancel_red_packet')]])
            )

        elif state == RED_PACKET_STATES["SET_COUNT"]:
            try:
                count = int(update.message.text.strip())
            except ValueError:
                await update.message.reply_text("⚠️ 请输入有效的整数")
                return
            
            if not 1 <= count <= RED_PACKET_MAX_COUNT:
                await update.message.reply_text(f"❌ 红包个数需在1-{RED_PACKET_MAX_COUNT}之间")
                return
            
            packet_id = RedPacketHandler.generate_id()
            amounts = RedPacketHandler.calculate_amounts(
                context.user_data['red_packet']['amount'], 
                count
            )
            
            context.user_data['red_packet'].update({
                'count': count,
                'id': packet_id,
                'state': RED_PACKET_STATES["CONFIRMING"],
                'amounts': amounts
            })
            
            confirm_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ 确认发送", callback_data='confirm_red_packet')],
                [InlineKeyboardButton("✏️ 修改金额", callback_data='modify_amount')],
                [InlineKeyboardButton("❌ 取消", callback_data='cancel_red_packet')]
            ])
            
            await update.message.reply_text(
                f"🎁 红包详情\n━━━━━━━━━━━━\n"
                f"总金额: {context.user_data['red_packet']['amount']} USDT\n"
                f"红包个数: {count}\n"
                f"━━━━━━━━━━━━\n"
                f"当前余额: {data['balance'][user_id]} USDT",
                reply_markup=confirm_keyboard
            )
    
    except Exception as e:
        logger.error(f"处理红包输入时出错: {str(e)}")
        await update.message.reply_text("⚠️ 处理请求时发生错误")
        context.user_data.pop('red_packet', None)

async def confirm_red_packet(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'confirm_red_packet':
        user_id = str(query.from_user.id)
        data = load_user_data()
        packet = context.user_data['red_packet']
        
        # 扣除余额
        data['balance'][user_id] -= packet['amount']
        
        # 保存红包数据
        data['red_packets'][packet['id']] = {
            'creator': user_id,
            'total': packet['amount'],
            'count': packet['count'],
            'remaining': packet['count'],
            'amounts': packet['amounts'],
            'claimed': {},
            'create_time': datetime.now().isoformat(),
            'group_id': None,
            'expire_time': (datetime.now() + timedelta(hours=24)).isoformat()
        }
        
        # 用户红包记录
        data['user_red_packets'][user_id] = data['user_red_packets'].get(user_id, []) + [packet['id']]
        
        save_user_data(data)
        
        # 生成转发消息
        share_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "📩 转发到群组",
                switch_inline_query=f"redpacket_{packet['id']}"
            )
        ]])
        
        await query.edit_message_text(
            f"✅ 红包创建成功！\n"
            f"红包ID: {packet['id']}\n"
            f"有效期至: {data['red_packets'][packet['id']]['expire_time'][:16]}",
            reply_markup=share_keyboard)
        
        context.user_data.pop('red_packet', None)

async def handle_group_red_packet(update: Update, context: CallbackContext):
    if not update.inline_query:
        return
    
    query = update.inline_query
    packet_id = query.query.split('_')[-1]
    data = load_user_data()
    
    if packet_id not in data['red_packets']:
        return
    
    packet = data['red_packets'][packet_id]
    results = [InlineQueryResultArticle(
        id=packet_id,
        title="点击发送红包到本群",
        input_message_content=InputTextMessageContent(
            f"🧧 红包来袭！\n"
            f"总金额: {packet['total']} USDT\n"
            f"个数: {packet['count']}\n"
            f"由用户 {query.from_user.mention_markdown()} 发送\n"
            f"━━━━━━━━━━━━\n"
            f"剩余: {packet['remaining']}/{packet['count']}",
            parse_mode='Markdown'
        ),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎁 领取红包", callback_data=f"claim_{packet_id}")]])
    )]
    
    await context.bot.answer_inline_query(query.id, results)

async def claim_red_packet(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    packet_id = query.data.split('_')[-1]
    data = load_user_data()
    
    if packet_id not in data['red_packets']:
        await query.edit_message_text("❌ 红包已过期")
        return
    
    packet = data['red_packets'][packet_id]
    
    # 检查是否已领取
    if user_id in packet['claimed']:
        await query.answer("您已经领过这个红包啦！")
        return
    
    # 检查是否过期
    if datetime.now() > datetime.fromisoformat(packet['expire_time']):
        await query.edit_message_text("⏳ 红包已过期")
        return
    
    # 分配金额
    try:
        amount = packet['amounts'].pop()
    except IndexError:
        await query.edit_message_text("🧧 红包已领完")
        return
    
    # 更新数据
    packet['remaining'] -= 1
    packet['claimed'][user_id] = amount
    data['balance'][user_id] = data['balance'].get(user_id, 0) + amount
    save_user_data(data)
    
    # 更新消息
    claim_info = "\n".join(
        [f"{uid[:4]}***: {amt} USDT" for uid, amt in packet['claimed'].items()]
    )
    
    await query.edit_message_text(
        f"🧧 红包详情\n━━━━━━━━━━━━\n"
        f"创建者: {packet['creator'][:4]}***\n"
        f"总金额: {packet['total']} USDT\n"
        f"已领取: {packet['count'] - packet['remaining']}/{packet['count']}\n"
        f"━━━━━━━━━━━━\n"
        f"领取记录:\n{claim_info[-200:]}\n"
        f"━━━━━━━━━━━━\n"
        f"剩余: {packet['remaining']}个 | 有效期至: {packet['expire_time'][:16]}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎁 领取红包", callback_data=f"claim_{packet_id}")]]) if packet['remaining'] > 0 else None
    )
    
    await query.answer(f"领取成功！获得 {amount} USDT")

async def show_my_packets(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    user_id = str(query.from_user.id)
    data = load_user_data()
    
    packets_info = []
    for pid in data['user_red_packets'].get(user_id, []):
        if pid in data['red_packets']:
            p = data['red_packets'][pid]
            status = "进行中" if datetime.now() < datetime.fromisoformat(p['expire_time']) else "已结束"
            packets_info.append(
                f"📆 {p['create_time'][:16]} | {p['total']} USDT\n"
                f"状态: {status} | 剩余: {p['remaining']}/{p['count']}"
            )
    
    await query.edit_message_text(
        f"📦 我的红包\n━━━━━━━━━━━━\n" + 
        ("\n━━━━━━━━━━━━\n".join(packets_info[:5]) if packets_info else "暂无红包记录") +
        "\n\n注：仅显示最近5个红包",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回主菜单", callback_data='back_to_main')]])
    )

async def check_expired_packets(context: CallbackContext):
    data = load_user_data()
    now = datetime.now()
    
    for packet_id in list(data['red_packets'].keys()):
        packet = data['red_packets'][packet_id]
        expire_time = datetime.fromisoformat(packet['expire_time'])
        
        if now > expire_time and packet['remaining'] > 0:
            # 退回剩余金额
            remaining = sum(packet['amounts'][-packet['remaining']:])
            data['balance'][packet['creator']] += remaining
            data['red_packets'].pop(packet_id)
    
    save_user_data(data)

# ===== 辅助功能 =====
async def button_handler(update: Update, context: CallbackContext) -> None:
    try:
        query = update.callback_query
        user_id = query.from_user.id
        data = load_user_data()
        
        await query.answer()
        
        if query.data == 'deposit':
            await query.edit_message_text(
                f"💰 充值地址\n━━━━━━━━━━━━\n"
                f"TRC20地址: `{TRON_ADDRESS}`\n"
                f"━━━━━━━━━━━━\n"
                f"• 最小充值: 10 USDT\n"
                f"• 自动到账，无需联系客服\n"
                f"• 充值后余额自动更新",
                parse_mode='Markdown'
            )
        
        elif query.data == 'withdraw':
            await query.edit_message_text(
                "💸 提现请联系客服 @example_cs\n"
                "━━━━━━━━━━━━\n"
                "• 最小提现: 50 USDT\n"
                "• 手续费: 2%\n"
                "• 24小时内处理"
            )
        
        elif query.data == 'check_balance':
            balance = data['balance'].get(str(user_id), 0)
            await query.edit_message_text(f"💰 当前余额: {balance} USDT")
        
        elif query.data == 'help':
            await show_help(update, context)
        
        elif query.data in ['roll_machine', 'roll_user']:
            try:
                if query.data == 'roll_machine':
                    dice_messages, dice_values = await send_dice(query.message, context)
                    result = calculate_result(dice_values)
                    bet_details = data['bets'].get(str(user_id), {})
                    await show_result(user_id, result, bet_details, data, context)
                    data['in_progress'][str(user_id)] = False
                    save_user_data(data)
                else:
                    data['pending_rolls'][str(user_id)] = []
                    save_user_data(data)
                    context.job_queue.run_once(
                        roll_timeout, 
                        30, 
                        data=user_id,
                        name=f"roll_timeout_{user_id}"
                    )
                    await query.edit_message_text("🎲 请连续发送3个骰子")
            except Exception as e:
                await query.edit_message_text(f"❌ 错误: {str(e)}")
    except Exception as e:
        logger.error(f"按钮处理失败: {str(e)}")

async def send_dice(message: Message, context: CallbackContext, num_dice=3):
    try:
        dice_messages = []
        dice_values = []
        for _ in range(num_dice):
            msg = await message.reply_dice(emoji="🎲")
            dice_messages.append(msg)
            dice_values.append(msg.dice.value)
        return dice_messages, dice_values
    except Exception as e:
        logger.error(f"发送骰子失败: {str(e)}")
        raise

def format_history(history):
    trends = []
    for idx, entry in enumerate(history[-10:], 1):
        total = entry['total']
        size = '大' if total > 10 else '小'
        parity = '单' if total % 2 else '双'
        trends.append(f"第{idx}期: {entry['values']} {total} {size}{parity}")
    return "\n".join(trends) if trends else "暂无历史"

async def show_result(user_id, result, bet_details, data, context, is_machine=True):
    try:
        winnings, winning_bets = calculate_winnings(bet_details, result)
        balance = data['balance'].get(str(user_id), 0)
        
        # 更新余额
        new_balance = balance + winnings
        data['balance'][str(user_id)] = new_balance
        
        # 保存历史记录
        data['history'].append({
            "time": datetime.now().isoformat(),
            "user": user_id,
            "dice": result['values'],
            "bet": bet_details,
            "result": result,
            "winnings": winnings
        })
        save_user_data(data)
        
        # 构建结果消息
        result_symbols = {
            'is_big': '大' if result['is_big'] else '小',
            'is_odd': '单' if result['is_odd'] else '双',
            'special': ''
        }
        
        if result['is_triple']:
            result_symbols['special'] = f"豹子{result['triple_num']}"
        elif result['is_straight']:
            result_symbols['special'] = "顺子"
        elif result['is_pair']:
            result_symbols['special'] = "对子"
        
        win_details = "\n".join([
            f"• {bet_type} ×{ODDS[bet_type]}" 
            for bet_type in winning_bets
        ]) if winning_bets else "无中奖项目"
        
        history = format_history(data['history'])
        
        await context.bot.send_message(
            chat_id=user_id,
            text=f"🎲 开奖结果\n━━━━━━━━━━━━\n"
                 f"骰子点数: {result['values']}\n"
                 f"总和: {result['total']} {result_symbols['special']}\n"
                 f"━━━━━━━━━━━━\n"
                 f"中奖项目:\n{win_details}\n"
                 f"盈利: {'+' if winnings>0 else ''}{winnings} USDT\n"
                 f"当前余额: {new_balance} USDT\n"
                 f"━━━━━━━━━━━━\n"
                 f"近期开奖记录:\n{history}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 继续游戏", callback_data='continue_game')],
                [InlineKeyboardButton("🏠 返回主菜单", callback_data='back_to_main')]
            ])
        )
    except Exception as e:
        logger.error(f"显示结果失败: {str(e)}")

async def show_help(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    help_text = (
        "🎮 游戏规则说明\n"
        "━━━━━━━━━━━━\n"
        "1. 下注格式示例:\n"
        "   • 大单100 豹子50\n"
        "   • 定位胆4 10（押总和为4）\n"
        "   • 豹5 20（押豹子5）\n\n"
        
        "2. 红包功能:\n"
        "   • 创建红包后可分享到群组\n"
        "   • 24小时未领完自动退回\n\n"
        
        "3. 充提说明:\n"
        "   • 提现联系客服 @example_cs\n"
        "   • TRC20自动充值即时到账\n\n"
        
        "4. 公平性:\n"
        "   • 基于Telegram官方骰子\n"
        "   • 开奖结果可追溯验证"
    )
    
    await query.edit_message_text(
        help_text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 返回主菜单", callback_data='back_to_main')]
        ])
    )

# ===== 主菜单处理 =====
async def back_to_main(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = load_user_data()
    
    keyboard = [
        [InlineKeyboardButton("💰 充值", callback_data='deposit'),
         InlineKeyboardButton("💸 提现", callback_data='withdraw')],
        [InlineKeyboardButton("💳 余额", callback_data='check_balance'),
         InlineKeyboardButton("🧧 发红包", callback_data='send_red_packet')],
        [InlineKeyboardButton("📦 我的红包", callback_data='my_packets'),
         InlineKeyboardButton("📖 帮助", callback_data='help')]
    ]
    
    await query.edit_message_text(
        f"🎲 骰子游戏系统\n━━━━━━━━━━━━\n"
        f"ID: {user_id}\n"
        f"余额: {data['balance'].get(str(user_id), 0)} USDT\n"
        f"━━━━━━━━━━━━\n"
        f"✅ TRC20自动充值\n"
        f"✅ 官方骰子公平公正",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ===== 定时任务 =====
def setup_job_queue(job_queue: JobQueue):
    job_queue.run_repeating(
        check_expired_packets,
        interval=300,  # 每5分钟检查一次
        first=10
    )

# ===== 错误处理 =====
async def error_handler(update: Update, context: CallbackContext):
    logger.error(msg="异常发生", exc_info=context.error)
    if update.effective_message:
        await update.effective_message.reply_text("⚠️ 系统繁忙，请稍后再试")

# ===== 主函数 =====
def main() -> None:
    application = Application.builder().token(TOKEN).build()
    
    # 管理员命令
    application.add_handler(CommandHandler("add", admin_add))
    application.add_handler(CommandHandler("set", admin_set))
    application.add_handler(CommandHandler("list", admin_list))
    application.add_handler(CommandHandler("logs", admin_logs))
    application.add_handler(CommandHandler("invite", admin_invite))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_commands))
    
    # 用户命令
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, place_bet))
    application.add_handler(MessageHandler(filters.Dice.DICE, handle_dice_result))
    
    # 红包功能
    application.add_handler(CallbackQueryHandler(handle_red_packet_creation, pattern='^send_red_packet$'))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_red_packet_input))
    application.add_handler(CallbackQueryHandler(confirm_red_packet, pattern='^confirm_red_packet$'))
    application.add_handler(InlineQueryHandler(handle_group_red_packet, pattern=r"^redpacket_"))
    application.add_handler(CallbackQueryHandler(claim_red_packet, pattern=r"^claim_"))
    application.add_handler(CallbackQueryHandler(show_my_packets, pattern='^my_packets$'))
    
    # 通用回调处理
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(CallbackQueryHandler(back_to_main, pattern='^back_to_main$'))
    
    # 设置定时任务
    setup_job_queue(application.job_queue)
    
    # 错误处理
    application.add_error_handler(error_handler)
    
    # 初始化数据文件
    if not DATA_FILE.exists():
        save_user_data({
            "balance": {},
            "logs": [],
            "bets": {},
            "pending_rolls": {},
            "history": [],
            "in_progress": {},
            "red_packets": {},
            "user_red_packets": {}
        })
    
    application.run_polling()

if __name__ == "__main__":
    main()

