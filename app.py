from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import uuid
import os
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-here')

# Database configuration
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith('postgres://'):
    # Fix for Render's PostgreSQL URL format
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///vote_log.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 5,
    'pool_recycle': 60,
    'pool_pre_ping': True
}

CORS(app)
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# ============================================================
# DATABASE MODELS
# ============================================================

class Room(db.Model):
    __tablename__ = 'rooms'
    
    id = db.Column(db.String(50), primary_key=True)
    available_positions = db.Column(db.Text, default='[]')  # JSON array
    timer_end = db.Column(db.BigInteger, nullable=True)  # Unix timestamp
    finalized = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    registrations = db.relationship('Registration', backref='room', lazy=True, cascade='all, delete-orphan')
    votes = db.relationship('Vote', backref='room', lazy=True, cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'availablePositions': json.loads(self.available_positions),
            'timerEnd': self.timer_end,
            'finalized': self.finalized,
            'createdAt': self.created_at.isoformat() if self.created_at else None,
            'registrations': {r.position: r.to_dict() for r in self.registrations},
            'votes': {v.voter_id: v.candidate_user_id for v in self.votes}
        }

class Registration(db.Model):
    __tablename__ = 'registrations'
    
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.String(50), db.ForeignKey('rooms.id'), nullable=False)
    position = db.Column(db.String(50), nullable=False)
    user_id = db.Column(db.String(50), nullable=False)
    user_name = db.Column(db.String(100), nullable=False)
    bio = db.Column(db.Text, default='')
    image = db.Column(db.Text, default='')  # Base64 or URL
    registered_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'userId': self.user_id,
            'userName': self.user_name,
            'bio': self.bio,
            'image': self.image,
            'position': self.position,
            'registeredAt': self.registered_at.isoformat() if self.registered_at else None
        }

class Vote(db.Model):
    __tablename__ = 'votes'
    
    id = db.Column(db.Integer, primary_key=True)
    room_id = db.Column(db.String(50), db.ForeignKey('rooms.id'), nullable=False)
    voter_id = db.Column(db.String(50), nullable=False)
    candidate_user_id = db.Column(db.String(50), nullable=False)
    voted_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('room_id', 'voter_id', name='unique_voter_per_room'),
    )

# ============================================================
# CREATE TABLES
# ============================================================

with app.app_context():
    db.create_all()
    print("✅ Database tables created successfully")

# ============================================================
# ROUTES
# ============================================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/room/<room_id>')
def view_room(room_id):
    room = Room.query.get(room_id)
    if not room:
        return render_template('index.html', error='Room not found', room_id=room_id)
    return render_template('index.html', room_id=room_id)

@app.route('/api/room/<room_id>', methods=['GET'])
def get_room(room_id):
    room = Room.query.get(room_id)
    if not room:
        return jsonify({'error': 'Room not found'}), 404
    return jsonify(room.to_dict())

@app.route('/api/room', methods=['POST'])
def create_room():
    data = request.json
    room_id = f"room-{uuid.uuid4().hex[:6]}"
    
    room = Room(
        id=room_id,
        available_positions=json.dumps(data.get('positions', [])),
        timer_end=data.get('timerEnd'),
        finalized=False
    )
    
    db.session.add(room)
    db.session.commit()
    
    return jsonify({'success': True, 'roomId': room_id, 'room': room.to_dict()})

@app.route('/api/room/<room_id>/registrations', methods=['GET'])
def get_registrations(room_id):
    room = Room.query.get(room_id)
    if not room:
        return jsonify({'error': 'Room not found'}), 404
    
    registrations = Registration.query.filter_by(room_id=room_id).all()
    return jsonify([r.to_dict() for r in registrations])

@app.route('/api/room/<room_id>/votes', methods=['GET'])
def get_votes(room_id):
    room = Room.query.get(room_id)
    if not room:
        return jsonify({'error': 'Room not found'}), 404
    
    votes = Vote.query.filter_by(room_id=room_id).all()
    return jsonify([{'voterId': v.voter_id, 'candidateUserId': v.candidate_user_id} for v in votes])

# ============================================================
# SOCKET.IO EVENTS
# ============================================================

@socketio.on('connect')
def handle_connect():
    print(f'Client connected: {request.sid}')

@socketio.on('disconnect')
def handle_disconnect():
    print(f'Client disconnected: {request.sid}')

@socketio.on('subscribe')
def handle_subscribe(data):
    room_id = data.get('roomId')
    if room_id:
        join_room(room_id)
        print(f'Client {request.sid} subscribed to room {room_id}')
        
        room = Room.query.get(room_id)
        if room:
            emit('room_update', {
                'type': 'room_update',
                'data': room.to_dict()
            }, room=request.sid)

@socketio.on('create_room')
def handle_create_room(data):
    room_id = f"room-{uuid.uuid4().hex[:6]}"
    
    room = Room(
        id=room_id,
        available_positions=json.dumps(data.get('positions', [])),
        timer_end=data.get('timerEnd'),
        finalized=False
    )
    
    db.session.add(room)
    db.session.commit()
    
    join_room(room_id)
    
    emit('room_created', {
        'type': 'room_created',
        'roomId': room_id,
        'data': room.to_dict()
    }, room=room_id)

@socketio.on('register')
def handle_register(data):
    room_id = data.get('roomId')
    position = data.get('position')
    user_name = data.get('userName')
    
    room = Room.query.get(room_id)
    if not room:
        emit('error', {'message': 'Room not found'}, room=request.sid)
        return
    
    # Check if position already taken
    existing = Registration.query.filter_by(room_id=room_id, position=position).first()
    if existing:
        emit('error', {'message': 'Position already taken'}, room=request.sid)
        return
    
    user_id = f"user_{uuid.uuid4().hex[:6]}"
    
    registration = Registration(
        room_id=room_id,
        position=position,
        user_id=user_id,
        user_name=user_name,
        bio='',
        image=''
    )
    
    db.session.add(registration)
    db.session.commit()
    
    # Refresh room data
    room = Room.query.get(room_id)
    
    emit('user_registered', {
        'type': 'user_registered',
        'data': room.to_dict()
    }, room=room_id)
    
    emit('registration_success', {
        'type': 'registration_success',
        'userId': user_id,
        'position': position
    }, room=request.sid)

@socketio.on('update_profile')
def handle_update_profile(data):
    room_id = data.get('roomId')
    user_id = data.get('userId')
    profile_data = data.get('profileData', {})
    
    registration = Registration.query.filter_by(room_id=room_id, user_id=user_id).first()
    if not registration:
        emit('error', {'message': 'User not found'}, room=request.sid)
        return
    
    if 'userName' in profile_data:
        registration.user_name = profile_data['userName']
    if 'bio' in profile_data:
        registration.bio = profile_data['bio']
    if 'image' in profile_data:
        registration.image = profile_data['image']
    
    db.session.commit()
    
    room = Room.query.get(room_id)
    emit('profile_updated', {
        'type': 'profile_updated',
        'data': room.to_dict()
    }, room=room_id)

@socketio.on('vote')
def handle_vote(data):
    room_id = data.get('roomId')
    voter_id = data.get('voterId')
    candidate_user_id = data.get('candidateUserId')
    
    room = Room.query.get(room_id)
    if not room:
        emit('error', {'message': 'Room not found'}, room=request.sid)
        return
    
    # Check if already voted
    existing = Vote.query.filter_by(room_id=room_id, voter_id=voter_id).first()
    if existing:
        emit('error', {'message': 'Already voted'}, room=request.sid)
        return
    
    # Check if candidate exists
    candidate = Registration.query.filter_by(room_id=room_id, user_id=candidate_user_id).first()
    if not candidate:
        emit('error', {'message': 'Candidate not found'}, room=request.sid)
        return
    
    vote = Vote(
        room_id=room_id,
        voter_id=voter_id,
        candidate_user_id=candidate_user_id
    )
    
    db.session.add(vote)
    db.session.commit()
    
    room = Room.query.get(room_id)
    emit('vote_cast', {
        'type': 'vote_cast',
        'data': room.to_dict()
    }, room=room_id)

@socketio.on('finalize_room')
def handle_finalize_room(data):
    room_id = data.get('roomId')
    
    room = Room.query.get(room_id)
    if not room:
        emit('error', {'message': 'Room not found'}, room=request.sid)
        return
    
    room.finalized = True
    db.session.commit()
    
    room = Room.query.get(room_id)
    emit('room_finalized', {
        'type': 'room_finalized',
        'data': room.to_dict()
    }, room=room_id)

# ============================================================
# RUN
# ============================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)