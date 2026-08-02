from flask import Flask, request, jsonify, render_template, session
from functools import wraps
from datetime import datetime, timedelta
from models import db, Player, PlayerEquip, MailRecord, RechargeRecord, OperationLog, ServerAnnouncement, GMAdmin
from config import Config
import json

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

with app.app_context():
    db.create_all()


# ==================== 权限装饰器 ====================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'gm_user' not in session:
            return jsonify({'code': 401, 'msg': '未登录'}), 401
        return f(*args, **kwargs)
    return decorated


def log_operation(gm_user, action, target_uid=None, detail=None, ip=None):
    """记录GM操作日志"""
    log = OperationLog(
        gm_user=gm_user,
        action=action,
        target_uid=target_uid,
        detail=json.dumps(detail, ensure_ascii=False) if detail else None,
        ip_address=ip or request.remote_addr
    )
    db.session.add(log)
    db.session.commit()


# ==================== 页面路由 ====================

@app.route('/')
def index():
    return jsonify({
        'code': 0,
        'msg': '一剑封天GM后台API运行中',
        'endpoints': {
            'login': '/api/login',
            'player_list': '/api/player/list',
            'dashboard': '/api/dashboard'
        }
    })



# ==================== 登录接口 ====================

@app.route('/api/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data.get('username', '')
    password = data.get('password', '')

    admin = GMAdmin.query.filter_by(username=username).first()
    if admin and admin.password_hash == password:  # 生产环境请用bcrypt
        session['gm_user'] = username
        session['gm_role'] = admin.role
        log_operation(username, '登录', ip=request.remote_addr)
        return jsonify({'code': 0, 'msg': '登录成功', 'data': {'role': admin.role}})

    return jsonify({'code': 403, 'msg': '账号或密码错误'}), 403


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'code': 0, 'msg': '已退出'})


# ==================== 玩家管理 ====================

@app.route('/api/player/list', methods=['GET'])
@login_required
def player_list():
    """查询玩家列表（支持搜索/分页）"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    keyword = request.args.get('keyword', '')
    server_id = request.args.get('server_id', type=int)
    status = request.args.get('status', '')

    query = Player.query
    if keyword:
        query = query.filter(
            db.or_(
                Player.player_uid.contains(keyword),
                Player.nickname.contains(keyword)
            )
        )
    if server_id:
        query = query.filter_by(server_id=server_id)
    if status:
        query = query.filter_by(status=status)

    pagination = query.order_by(Player.id.desc()).paginate(page=page, per_page=per_page)
    players = [{
        'id': p.id,
        'player_uid': p.player_uid,
        'nickname': p.nickname,
        '职业': p.职业,
        'level': p.level,
        'vip_level': p.vip_level,
        'combat_power': p.combat_power,
        'diamond': p.diamond,
        'gold': p.gold,
        'status': p.status,
        'server_id': p.server_id,
        'last_login': p.last_login.strftime('%Y-%m-%d %H:%M') if p.last_login else '-',
    } for p in pagination.items]

    return jsonify({
        'code': 0,
        'data': {
            'list': players,
            'total': pagination.total,
            'page': page,
            'per_page': per_page
        }
    })


@app.route('/api/player/detail/<player_uid>', methods=['GET'])
@login_required
def player_detail(player_uid):
    """查询玩家详情（含装备）"""
    player = Player.query.filter_by(player_uid=player_uid).first()
    if not player:
        return jsonify({'code': 404, 'msg': '玩家不存在'}), 404

    equips = PlayerEquip.query.filter_by(player_uid=player_uid).all()
    equip_list = [{
        'equip_id': e.equip_id,
        'equip_name': e.equip_name,
        'equip_type': e.equip_type,
        'quality': e.quality,
        'level': e.level,
        'enhance_level': e.enhance_level,
        'is_equipped': e.is_equipped
    } for e in equips]

    return jsonify({
        'code': 0,
        'data': {
            'player': {
                'player_uid': player.player_uid,
                'nickname': player.nickname,
                '职业': player.职业,
                'level': player.level,
                'vip_level': player.vip_level,
                'combat_power': player.combat_power,
                'diamond': player.diamond,
                'gold': player.gold,
                'exp': player.exp,
                'status': player.status,
                'ban_reason': player.ban_reason,
                'server_id': player.server_id,
                'created_at': player.created_at.strftime('%Y-%m-%d %H:%M'),
                'last_login': player.last_login.strftime('%Y-%m-%d %H:%M') if player.last_login else '-'
            },
            'equips': equip_list
        }
    })


# ==================== GM操作：修改玩家数据 ====================

@app.route('/api/gm/modify_player', methods=['POST'])
@login_required
def modify_player():
    """GM修改玩家属性（灵玉/金币/等级/VIP等）"""
    data = request.get_json()
    player_uid = data.get('player_uid')
    modifications = data.get('modifications', {})

    player = Player.query.filter_by(player_uid=player_uid).first()
    if not player:
        return jsonify({'code': 404, 'msg': '玩家不存在'}), 404

    allowed_fields = {
        'diamond': int, 'gold': int, 'level': int,
        'vip_level': int, 'combat_power': int, 'exp': int
    }
    changes = {}
    for field, cast_type in allowed_fields.items():
        if field in modifications:
            old_val = getattr(player, field)
            new_val = cast_type(modifications[field])
            setattr(player, field, new_val)
            changes[field] = {'old': old_val, 'new': new_val}

    db.session.commit()
    log_operation(
        session['gm_user'], '修改玩家数据',
        target_uid=player_uid,
        detail={'modifications': changes}
    )

    return jsonify({'code': 0, 'msg': '修改成功', 'data': changes})


# ==================== GM操作：发放道具 ====================

@app.route('/api/gm/send_item', methods=['POST'])
@login_required
def send_item():
    """GM给玩家发放道具/装备"""
    data = request.get_json()
    player_uid = data.get('player_uid')
    items = data.get('items', [])  # [{"item_id": 1001, "name": "九天玄铁剑", "count": 1, "type": "weapon", "quality": "橙"}]

    player = Player.query.filter_by(player_uid=player_uid).first()
    if not player:
        return jsonify({'code': 404, 'msg': '玩家不存在'}), 404

    sent_items = []
    for item in items:
        equip = PlayerEquip(
            player_uid=player_uid,
            equip_id=item.get('item_id'),
            equip_name=item.get('name', '未知道具'),
            equip_type=item.get('type', 'material'),
            quality=item.get('quality', '白'),
            level=item.get('level', 1),
            enhance_level=item.get('enhance_level', 0),
            is_equipped=False
        )
        db.session.add(equip)
        sent_items.append(item.get('name', '未知道具'))

    db.session.commit()
    log_operation(
        session['gm_user'], '发放道具',
        target_uid=player_uid,
        detail={'items': sent_items}
    )

    return jsonify({'code': 0, 'msg': f'成功发放 {len(sent_items)} 个道具'})


# ==================== GM操作：发送邮件 ====================

@app.route('/api/gm/send_mail', methods=['POST'])
@login_required
def send_mail():
    """GM发送邮件（支持单人/全服）"""
    data = request.get_json()
    receiver_uid = data.get('receiver_uid', '*')
    title = data.get('title', '')
    content = data.get('content', '')
    attachments = data.get('attachments', [])
    is_global = (receiver_uid == '*')

    if not title or not content:
        return jsonify({'code': 400, 'msg': '标题和内容不能为空'}), 400

    mail = MailRecord(
        sender=session['gm_user'],
        receiver_uid=receiver_uid,
        title=title,
        content=content,
        attachments=attachments,
        is_global=is_global
    )
    db.session.add(mail)
    db.session.commit()

    log_operation(
        session['gm_user'], '发送邮件',
        target_uid=receiver_uid,
        detail={'title': title, 'is_global': is_global, 'attachments': attachments}
    )

    scope = '全服' if is_global else f'玩家[{receiver_uid}]'
    return jsonify({'code': 0, 'msg': f'邮件已发送至{scope}'})


# ==================== GM操作：封号/禁言 ====================

@app.route('/api/gm/ban_player', methods=['POST'])
@login_required
def ban_player():
    """封号或禁言"""
    data = request.get_json()
    player_uid = data.get('player_uid')
    action_type = data.get('action_type')  # ban / mute / unban
    reason = data.get('reason', '')
    duration_hours = data.get('duration_hours', 0)  # 0表示永久

    player = Player.query.filter_by(player_uid=player_uid).first()
    if not player:
        return jsonify({'code': 404, 'msg': '玩家不存在'}), 404

    if action_type == 'ban':
        player.status = 'banned'
        player.ban_reason = reason
        player.ban_expire = datetime.utcnow() + timedelta(hours=duration_hours) if duration_hours > 0 else None
    elif action_type == 'mute':
        player.status = 'muted'
        player.ban_reason = reason
        player.ban_expire = datetime.utcnow() + timedelta(hours=duration_hours) if duration_hours > 0 else None
    elif action_type == 'unban':
        player.status = 'normal'
        player.ban_reason = None
        player.ban_expire = None

    db.session.commit()
    log_operation(
        session['gm_user'], f'玩家{action_type}',
        target_uid=player_uid,
        detail={'reason': reason, 'duration_hours': duration_hours}
    )

    action_map = {'ban': '封号', 'mute': '禁言', 'unban': '解封'}
    return jsonify({'code': 0, 'msg': f'已{action_map.get(action_type, "操作")}玩家 [{player_uid}]'})


# ==================== 充值记录查询 ====================

@app.route('/api/recharge/list', methods=['GET'])
@login_required
def recharge_list():
    """查询充值记录"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    player_uid = request.args.get('player_uid', '')

    query = RechargeRecord.query
    if player_uid:
        query = query.filter_by(player_uid=player_uid)

    pagination = query.order_by(RechargeRecord.id.desc()).paginate(page=page, per_page=per_page)
    records = [{
        'id': r.id,
        'player_uid': r.player_uid,
        'order_id': r.order_id,
        'amount': r.amount,
        'diamond_added': r.diamond_added,
        'product_name': r.product_name,
        'status': r.status,
        'created_at': r.created_at.strftime('%Y-%m-%d %H:%M')
    } for r in pagination.items]

    return jsonify({
        'code': 0,
        'data': {'list': records, 'total': pagination.total, 'page': page}
    })


# ==================== 全服公告管理 ====================

@app.route('/api/announcement/list', methods=['GET'])
@login_required
def announcement_list():
    announcements = ServerAnnouncement.query.order_by(ServerAnnouncement.id.desc()).all()
    return jsonify({
        'code': 0,
        'data': [{
            'id': a.id,
            'title': a.title,
            'content': a.content,
            'announce_type': a.announce_type,
            'is_active': a.is_active,
            'created_at': a.created_at.strftime('%Y-%m-%d %H:%M')
        } for a in announcements]
    })


@app.route('/api/announcement/create', methods=['POST'])
@login_required
def create_announcement():
    data = request.get_json()
    ann = ServerAnnouncement(
        title=data.get('title', ''),
        content=data.get('content', ''),
        announce_type=data.get('announce_type', 'normal'),
        created_by=session['gm_user']
    )
    db.session.add(ann)
    db.session.commit()
    log_operation(session['gm_user'], '发布公告', detail={'title': ann.title})
    return jsonify({'code': 0, 'msg': '公告发布成功'})


# ==================== 操作日志查询 ====================

@app.route('/api/log/list', methods=['GET'])
@login_required
def log_list():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    gm_user = request.args.get('gm_user', '')
    action = request.args.get('action', '')

    query = OperationLog.query
    if gm_user:
        query = query.filter_by(gm_user=gm_user)
    if action:
        query = query.filter_by(action=action)

    pagination = query.order_by(OperationLog.id.desc()).paginate(page=page, per_page=per_page)
    logs = [{
        'id': l.id,
        'gm_user': l.gm_user,
        'action': l.action,
        'target_uid': l.target_uid,
        'detail': l.detail,
        'ip_address': l.ip_address,
        'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
    } for l in pagination.items]

    return jsonify({
        'code': 0,
        'data': {'list': logs, 'total': pagination.total, 'page': page}
    })


# ==================== 数据看板 ====================

@app.route('/api/dashboard', methods=['GET'])
@login_required
def dashboard():
    """首页数据看板"""
    total_players = Player.query.count()
    online_today = Player.query.filter(
        Player.last_login >= datetime.utcnow().replace(hour=0, minute=0, second=0)
    ).count()
    total_recharge = db.session.query(db.func.sum(RechargeRecord.amount)).scalar() or 0
    banned_count = Player.query.filter_by(status='banned').count()

    return jsonify({
        'code': 0,
        'data': {
            'total_players': total_players,
            'online_today': online_today,
            'total_recharge': round(total_recharge, 2),
            'banned_count': banned_count
        }
    })


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

 from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class GMAdmin(db.Model):
    """GM管理员表"""
    __tablename__ = 'gm_admin'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(32), default='operator')  # admin / operator
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Player(db.Model):
    """玩家角色表"""
    __tablename__ = 'player'
    id = db.Column(db.Integer, primary_key=True)
    player_uid = db.Column(db.String(64), unique=True, nullable=False, index=True)
    nickname = db.Column(db.String(64), nullable=False)
   职业 = db.Column(db.String(32))  # 云锋 / 魅狐 / 幽冥 / 星陨
    level = db.Column(db.Integer, default=1)
    vip_level = db.Column(db.Integer, default=0)
    combat_power = db.Column(db.BigInteger, default=0)
    diamond = db.Column(db.BigInteger, default=0)       # 灵玉
    gold = db.Column(db.BigInteger, default=0)           # 金币
    exp = db.Column(db.BigInteger, default=0)
    status = db.Column(db.String(16), default='normal')  # normal / banned / muted
    ban_reason = db.Column(db.String(256))
    ban_expire = db.Column(db.DateTime)
    server_id = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)


class PlayerEquip(db.Model):
    """玩家装备表"""
    __tablename__ = 'player_equip'
    id = db.Column(db.Integer, primary_key=True)
    player_uid = db.Column(db.String(64), db.ForeignKey('player.player_uid'), nullable=False)
    equip_id = db.Column(db.Integer, nullable=False)
    equip_name = db.Column(db.String(128))
    equip_type = db.Column(db.String(32))   # weapon / armor / wing / mount / fashion
    quality = db.Column(db.String(16))      # 白/绿/蓝/紫/橙/红
    level = db.Column(db.Integer, default=1)
    enhance_level = db.Column(db.Integer, default=0)
    gem_slots = db.Column(db.JSON)          # 宝石镶嵌信息
    is_equipped = db.Column(db.Boolean, default=False)


class MailRecord(db.Model):
    """GM邮件发送记录"""
    __tablename__ = 'mail_record'
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(64), nullable=False)       # 发送者GM账号
    receiver_uid = db.Column(db.String(64))                 # 目标玩家UID，'*'表示全服
    receiver_name = db.Column(db.String(64))
    title = db.Column(db.String(128), nullable=False)
    content = db.Column(db.Text, nullable=False)
    attachments = db.Column(db.JSON)                        # [{"item_id": 1001, "name": "灵玉", "count": 500}]
    is_global = db.Column(db.Boolean, default=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)


class RechargeRecord(db.Model):
    """充值记录表"""
    __tablename__ = 'recharge_record'
    id = db.Column(db.Integer, primary_key=True)
    player_uid = db.Column(db.String(64), nullable=False, index=True)
    order_id = db.Column(db.String(128), unique=True, nullable=False)
    amount = db.Column(db.Float, nullable=False)            # 充值金额（元）
    diamond_added = db.Column(db.Integer, default=0)        # 获得灵玉
    product_name = db.Column(db.String(128))
    status = db.Column(db.String(16), default='success')    # success / pending / refund
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class OperationLog(db.Model):
    """GM操作日志"""
    __tablename__ = 'operation_log'
    id = db.Column(db.Integer, primary_key=True)
    gm_user = db.Column(db.String(64), nullable=False)
    action = db.Column(db.String(64), nullable=False)       # 操作类型
    target_uid = db.Column(db.String(64))
    detail = db.Column(db.Text)                             # 操作详情JSON
    ip_address = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ServerAnnouncement(db.Model):
    """全服公告表"""
    __tablename__ = 'server_announcement'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(128), nullable=False)
    content = db.Column(db.Text, nullable=False)
    announce_type = db.Column(db.String(16), default='normal')  # normal / maintenance / activity
    is_active = db.Column(db.Boolean, default=True)
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)
    created_by = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


 import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'yjft_gm_secret_2026')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///gm_yjft.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # GM管理员账号（生产环境应使用加密存储）
    GM_ADMIN_USER = 'admin'
    GM_ADMIN_PASS = 'yjft@gm2026'
 


