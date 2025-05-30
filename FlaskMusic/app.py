from flask import Flask, request, render_template, redirect, url_for, flash, make_response, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
import os
import json
import pylast
from datetime import datetime
import random

# --- Create the application instance ---
app = Flask(__name__)

# Secret key for sessions (use environment variable if possible)
app.secret_key = os.environ.get('SECRET_KEY', 'your_secret_key')

# --- Configure database ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(BASE_DIR, 'users.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- Configure upload settings ---
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Create necessary folders
os.makedirs(os.path.join(BASE_DIR, 'static'), exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- Initialize extensions ---
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- Last.fm API setup ---
API_KEY = '2bbf4f41f9948d580841b4ad251a1e1c'
API_SECRET = 'd8469f12eeca4373ac6815213c4727fc'
network = pylast.LastFMNetwork(api_key=API_KEY, api_secret=API_SECRET)

# --- Mood to tag mappings for Last.fm ---
MOOD_TAGS = {
    "happy": ["happy", "upbeat", "feel good", "uplifting", "cheerful"],
    "sad": ["sad", "melancholy", "melancholic", "heartbreak", "emotional"],
    "energetic": ["energetic", "pump up", "workout", "dance", "party"],
    "relaxed": ["relaxing", "chill", "ambient", "calm", "mellow"],
    "focused": ["focus", "concentration", "study", "instrumental", "background"]
}


# --- Helper for uploads ---
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# --- User model ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    profile_pic = db.Column(db.String(255), default='default-profile.png')
    favorite_artist = db.Column(db.String(255), default='')
    favorite_artist_image = db.Column(db.Text, default=None)  # New field
    favorite_song = db.Column(db.String(255), default='')
    favorite_song_image = db.Column(db.Text, default=None)  # New field
    favorite_genre = db.Column(db.String(255), default='')
    recent_vibes = db.Column(db.Text, default='[]')
    recent_artists = db.Column(db.Text, default='[]')
    bio = db.Column(db.Text, default='')
    joined_date = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<User {self.username}>'


# --- User loader for Flask-Login ---
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --- Handle unauthorized access for API calls ---
@login_manager.unauthorized_handler
def unauthorized_callback():
    if request.is_json or request.accept_mimetypes.accept_json:
        return make_response(jsonify({'status': 'error', 'message': 'Unauthorized'}), 401)
    else:
        return redirect(url_for('login'))


# --- Initialize database and create default user if empty ---
with app.app_context():
    db.create_all()
    if User.query.count() == 0:
        test_user = User(
            username='test',
            email='test@example.com',
            password='test',
            profile_pic='default-profile.png'
        )
        db.session.add(test_user)
        db.session.commit()


# Last.fm recommendation functions

def get_recommendations_by_song(track_name, artist_name=None, num_recommendations=10):
    """Find a song on Last.fm and recommend similar tracks with album artwork."""
    try:
        # If we have both track and artist, use them together
        if artist_name:
            track = network.get_track(artist_name, track_name)
        else:
            # Search for the track
            search_results = network.search_for_track("", track_name)
            matches = search_results.get_next_page()
            if not matches:
                return None  # No match found

            # Use the first match
            track = matches[0]

        # Get similar tracks - request more than needed to ensure unique results
        similar_tracks = track.get_similar(limit=num_recommendations * 2)

        # Track IDs to avoid duplicates
        seen_tracks = set()

        # Format the recommendations
        formatted_recs = []
        for similar in similar_tracks:
            if len(formatted_recs) >= num_recommendations:
                break

            track_obj = similar.item
            artist_obj = track_obj.get_artist()

            # Create a unique ID for this track
            track_id = f"{track_obj.get_name().lower()}_{artist_obj.get_name().lower()}"

            # Skip if we've already seen this track
            if track_id in seen_tracks:
                continue

            seen_tracks.add(track_id)

            # Try to get album information and cover art
            album_image = None
            try:
                # Get top albums from this artist
                top_albums = artist_obj.get_top_albums(limit=3)
                if top_albums:
                    # Use the first album's image
                    album = top_albums[0].item
                    album_image = album.get_cover_image(size=3)  # Size 3 is medium sized image
            except:
                # Fallback if we can't get album image
                album_image = None

            formatted_recs.append({
                'track_name': track_obj.get_name(),
                'artist_name': artist_obj.get_name(),
                'genre': 'Unknown',
                'url': track_obj.get_url(),
                'image_url': album_image
            })

        return formatted_recs
    except pylast.WSError as e:
        print(f"Last.fm API error: {e}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None


def get_recommendations_by_artist(artist_name, num_recommendations=10):
    """Find an artist on Last.fm and recommend similar artists with better image handling."""
    try:
        artist = network.get_artist(artist_name)

        # Get similar artists - request more than needed to ensure unique results
        similar_artists = artist.get_similar(limit=num_recommendations * 2)

        # Artist IDs to avoid duplicates
        seen_artists = set()

        # Format the results
        formatted_recs = []
        for similar in similar_artists:
            if len(formatted_recs) >= num_recommendations:
                break

            artist_obj = similar.item

            # Create a unique ID for this artist
            artist_id = artist_obj.get_name().lower()

            # Skip if we've already seen this artist
            if artist_id in seen_artists:
                continue

            seen_artists.add(artist_id)

            # Try to get top tags (genres) for this artist
            try:
                top_tags = artist_obj.get_top_tags(limit=1)
                genre = top_tags[0].item.get_name() if top_tags else 'Unknown'
            except:
                genre = 'Unknown'

            # Try multiple approaches to get artist image
            artist_image = None

            # First try to get artist image directly
            try:
                artist_image = artist_obj.get_cover_image(size=3)
            except:
                artist_image = None

            # If direct method fails, try getting from their top album
            if not artist_image:
                try:
                    top_albums = artist_obj.get_top_albums(limit=1)
                    if top_albums and len(top_albums) > 0:
                        album = top_albums[0].item
                        artist_image = album.get_cover_image(size=3)
                except:
                    pass

            # If that fails, try searching for the artist
            if not artist_image:
                try:
                    search_results = network.search_for_artist(artist_obj.get_name())
                    first_match = search_results.get_next_page()[0].item
                    artist_image = first_match.get_cover_image(size=3)
                except:
                    artist_image = None

            formatted_recs.append({
                'name': artist_obj.get_name(),
                'genre': genre,
                'url': artist_obj.get_url(),
                'image_url': artist_image
            })

        return formatted_recs
    except pylast.WSError as e:
        print(f"Last.fm API error: {e}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None


def get_recommendations_by_mood(mood, num_recommendations=10):
    """Get music recommendations based on mood tags from Last.fm."""
    if mood not in MOOD_TAGS:
        return None

    tags = MOOD_TAGS[mood]

    try:
        # Get tracks for the first tag (we could average multiple tags, but keeping it simple)
        tag = network.get_tag(tags[0])
        tracks = tag.get_top_tracks(limit=num_recommendations * 2)

        # Track IDs to avoid duplicates
        seen_tracks = set()

        # Format the recommendations
        formatted_recs = []
        for track in tracks:
            if len(formatted_recs) >= num_recommendations:
                break

            track_obj = track.item
            artist_obj = track_obj.get_artist()

            # Create a unique ID for this track
            track_id = f"{track_obj.get_name().lower()}_{artist_obj.get_name().lower()}"

            # Skip if we've already seen this track
            if track_id in seen_tracks:
                continue

            seen_tracks.add(track_id)

            # Try to get album information and cover art
            album_image = None
            try:
                # Get top albums from this artist
                top_albums = artist_obj.get_top_albums(limit=3)
                if top_albums:
                    # Use the first album's image
                    album = top_albums[0].item
                    album_image = album.get_cover_image(size=3)
            except:
                album_image = None

            formatted_recs.append({
                'track_name': track_obj.get_name(),
                'artist_name': artist_obj.get_name(),
                'genre': tags[0].capitalize(),  # Use the mood as genre
                'url': track_obj.get_url(),
                'image_url': album_image
            })

        return formatted_recs
    except pylast.WSError as e:
        print(f"Last.fm API error: {e}")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None


def recommend_music(song, artist):
    """Process user input and return stylized music recommendations with images."""
    result_html = ""

    if song:
        recommendations = get_recommendations_by_song(song, artist, num_recommendations=10)

        if recommendations:
            result_html += f"""
            <div class="recommendation-header">
                <h2>Based on <span class="highlight">{song}</span></h2>
                <p class="rec-subheading">You might enjoy these tracks:</p>
            </div>
            """
            result_html += '<div class="recommendation-grid">'

            for rec in recommendations:
                image_url = rec.get('image_url') or '/static/default-album.svg'

                result_html += f'''
                <div class="recommendation-card">
                    <div class="album-cover">
                        <img src="{image_url}" alt="Album cover for {rec['track_name']}">
                        <div class="play-overlay">
                            <i class="fas fa-play-circle"></i>
                        </div>
                    </div>
                    <div class="rec-info">
                        <h3 class="song-title">{rec['track_name']}</h3>
                        <p class="artist-name">{rec['artist_name']}</p>
                        <div class="button-group">
                            <a href="{rec['url']}" target="_blank" class="lastfm-link">
                                <i class="fas fa-external-link-alt"></i> Last.fm
                            </a>
                            <button class="add-button" onclick="addToPlaylist('{rec['track_name']}', '{rec['artist_name']}')">
                                <i class="fas fa-plus"></i> Add
                            </button>
                        </div>
                    </div>
                </div>
                '''

            result_html += '</div>'
            return result_html

    if artist:
        artist_recommendations = get_recommendations_by_artist(artist, num_recommendations=10)

        if artist_recommendations:
            result_html += f"""
            <div class="recommendation-header">
                <h2>Based on <span class="highlight">{artist}</span></h2>
                <p class="rec-subheading">You might enjoy these artists:</p>
            </div>
            """
            result_html += '<div class="recommendation-grid">'

            for rec in artist_recommendations:
                image_url = rec.get('image_url') or '/static/default-artist.svg'

                result_html += f'''
                <div class="recommendation-card">
                    <div class="artist-image">
                        <img src="{image_url}" alt="Image of {rec['name']}">
                        <div class="play-overlay">
                            <i class="fas fa-play-circle"></i>
                        </div>
                    </div>
                    <div class="rec-info">
                        <h3 class="artist-title">{rec['name']}</h3>
                        <p class="genre-name">Genre: {rec['genre']}</p>
                        <div class="button-group">
                            <a href="{rec['url']}" target="_blank" class="lastfm-link">
                                <i class="fas fa-external-link-alt"></i> Last.fm
                            </a>
                            <button class="add-button" onclick="addArtistToPlaylist('{rec['name']}')">
                                <i class="fas fa-plus"></i> Add
                            </button>
                        </div>
                    </div>
                </div>
                '''

            result_html += '</div>'
            return result_html

    return "No recommendations found. Try another song or artist."


def render_mood_recommendations(mood):
    """Create HTML for mood-based recommendations."""
    recommendations = get_recommendations_by_mood(mood)

    if not recommendations:
        return f"No recommendations found for {mood} mood. Please try another mood."

    result_html = f"""
    <div class="recommendation-header">
        <h2>Music for your <span class="highlight">{mood}</span> mood</h2>
        <p class="rec-subheading">Here are some tracks that match your mood:</p>
    </div>
    """
    result_html += '<div class="recommendation-grid">'

    for rec in recommendations:
        image_url = rec.get('image_url') or '/static/default-album.svg'

        result_html += f'''
        <div class="recommendation-card">
            <div class="album-cover">
                <img src="{image_url}" alt="Album cover for {rec['track_name']}">
                <div class="play-overlay">
                    <i class="fas fa-play-circle"></i>
                </div>
            </div>
            <div class="rec-info">
                <h3 class="song-title">{rec['track_name']}</h3>
                <p class="artist-name">{rec['artist_name']}</p>
                <div class="button-group">
                    <a href="{rec['url']}" target="_blank" class="lastfm-link">
                        <i class="fas fa-external-link-alt"></i> Last.fm
                    </a>
                    <button class="add-button" onclick="addToPlaylist('{rec['track_name']}', '{rec['artist_name']}')">
                        <i class="fas fa-plus"></i> Add
                    </button>
                </div>
            </div>
        </div>
        '''

    result_html += '</div>'
    return result_html


# Routes
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')

        if User.query.filter_by(username=username).first():
            flash('Username already exists!', 'error')
        elif User.query.filter_by(email=email).first():
            flash('Email already exists!', 'error')
        else:
            new_user = User(username=username, email=email, password=password)
            db.session.add(new_user)
            try:
                db.session.commit()
                flash('Account created successfully!', 'success')
                return redirect(url_for('login'))
            except Exception:
                db.session.rollback()
                flash('Error creating account. Please try again.', 'error')
    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.password == password:
            login_user(user)
            flash('Logged in successfully!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password!', 'error')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


@app.route('/profile')
@login_required
def profile():
    recent_vibes = []
    recent_artists = []
    try:
        recent_vibes = json.loads(current_user.recent_vibes)
    except:
        pass
    try:
        recent_artists = json.loads(current_user.recent_artists)
    except:
        pass

    if current_user.profile_pic != 'default-profile.png':
        pic_path = os.path.join(app.config['UPLOAD_FOLDER'], current_user.profile_pic)
        if not os.path.exists(pic_path):
            current_user.profile_pic = 'default-profile.png'
            try:
                db.session.commit()
            except:
                db.session.rollback()

    return render_template('profile.html', user=current_user, recent_vibes=recent_vibes, recent_artists=recent_artists)


@app.route('/update_profile_pic', methods=['POST'])
@login_required
def update_profile_pic():
    if 'profile_pic' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file part'}), 400

    file = request.files['profile_pic']
    if file.filename == '':
        return jsonify({'status': 'error', 'message': 'No selected file'}), 400

    if file and allowed_file(file.filename):
        # Handle the old profile picture
        if current_user.profile_pic != 'default-profile.png':
            old_pic = os.path.join(app.config['UPLOAD_FOLDER'], current_user.profile_pic)
            if os.path.exists(old_pic):
                os.remove(old_pic)

        # Save the new file
        filename = secure_filename(file.filename)
        unique_name = f"{current_user.username}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
        file.save(file_path)

        # Update the user's profile pic
        current_user.profile_pic = unique_name
        try:
            db.session.commit()
            return jsonify({
                'status': 'success',
                'image_url': url_for('static', filename=f'uploads/{unique_name}')
            })
        except Exception as e:
            db.session.rollback()
            return jsonify({'status': 'error', 'message': str(e)}), 500

    return jsonify({'status': 'error', 'message': 'File type not allowed'}), 400


@app.route('/', methods=['GET', 'POST'])
def home():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return redirect(url_for('login'))  # Redirect to login instead of home


@app.route('/index', methods=['GET', 'POST'])
@login_required
def index():
    output = None
    if request.method == 'POST':
        song = request.form.get('song', '').strip()
        artist = request.form.get('artist', '').strip()
        is_refresh = request.form.get('refresh') == 'true'

        if not song and not artist:
            output = "Please enter a song or an artist."
        else:
            try:
                # For refresh requests, we'll use a different approach
                if is_refresh:
                    # Get more recommendations than we need so we can randomize
                    excess_factor = 2  # Get 2x more recs than we'll show
                    recs = []

                    if song:
                        # Get song recommendations with higher limit
                        all_recs = get_recommendations_by_song(song, artist,
                                                               num_recommendations=10 * excess_factor)
                        if all_recs and len(all_recs) > 10:
                            # Randomly sample 10 recommendations
                            recs = random.sample(all_recs, 10)
                        else:
                            recs = all_recs

                        # Generate HTML with these randomized recommendations
                        if recs:
                            result_html = f"""
                            <div class="recommendation-header">
                                <h2>Based on <span class="highlight">{song}</span></h2>
                                <p class="rec-subheading">You might enjoy these tracks:</p>
                            </div>
                            """
                            result_html += '<div class="recommendation-grid">'

                            for rec in recs:
                                image_url = rec.get('image_url') or '/static/default-album.svg'

                                result_html += f'''
                                <div class="recommendation-card">
                                    <div class="album-cover">
                                        <img src="{image_url}" alt="Album cover for {rec['track_name']}">
                                        <div class="play-overlay">
                                            <i class="fas fa-play-circle"></i>
                                        </div>
                                    </div>
                                    <div class="rec-info">
                                        <h3 class="song-title">{rec['track_name']}</h3>
                                        <p class="artist-name">{rec['artist_name']}</p>
                                        <div class="button-group">
                                            <a href="{rec['url']}" target="_blank" class="lastfm-link">
                                                <i class="fas fa-external-link-alt"></i> Last.fm
                                            </a>
                                            <button class="add-button" onclick="addToPlaylist('{rec['track_name']}', '{rec['artist_name']}')">
                                                <i class="fas fa-plus"></i> Add
                                            </button>
                                        </div>
                                    </div>
                                </div>
                                '''

                            result_html += '</div>'
                            output = result_html
                        else:
                            output = "No recommendations found. Try another song or artist."

                    elif artist:
                        # Get artist recommendations with higher limit
                        all_recs = get_recommendations_by_artist(artist,
                                                                 num_recommendations=10 * excess_factor)
                        if all_recs and len(all_recs) > 10:
                            # Randomly sample 10 recommendations
                            recs = random.sample(all_recs, 10)
                        else:
                            recs = all_recs

                        # Generate HTML with these randomized recommendations
                        if recs:
                            result_html = f"""
                            <div class="recommendation-header">
                                <h2>Based on <span class="highlight">{artist}</span></h2>
                                <p class="rec-subheading">You might enjoy these artists:</p>
                            </div>
                            """
                            result_html += '<div class="recommendation-grid">'

                            for rec in recs:
                                image_url = rec.get('image_url') or '/static/default-artist.svg'

                                result_html += f'''
                                <div class="recommendation-card">
                                    <div class="artist-image">
                                        <img src="{image_url}" alt="Image of {rec['name']}">
                                        <div class="play-overlay">
                                            <i class="fas fa-play-circle"></i>
                                        </div>
                                    </div>
                                    <div class="rec-info">
                                        <h3 class="artist-title">{rec['name']}</h3>
                                        <p class="genre-name">Genre: {rec['genre']}</p>
                                        <div class="button-group">
                                            <a href="{rec['url']}" target="_blank" class="lastfm-link">
                                                <i class="fas fa-external-link-alt"></i> Last.fm
                                            </a>
                                            <button class="add-button" onclick="addArtistToPlaylist('{rec['name']}')">
                                                <i class="fas fa-plus"></i> Add
                                            </button>
                                        </div>
                                    </div>
                                </div>
                                '''

                            result_html += '</div>'
                            output = result_html
                        else:
                            output = "No recommendations found. Try another song or artist."
                else:
                    # Normal behavior for initial search
                    output = recommend_music(song, artist)
            except Exception as e:
                output = f"Error getting recommendations: {str(e)}"
                print(f"Last.fm API error: {e}")

    return render_template('index.html', output=output)


@app.route('/mood_recommendations', methods=['POST'])
@login_required
def mood_recommendations():
    data = request.get_json()
    mood = data.get('mood')
    is_refresh = data.get('refresh', False)

    if not mood:
        return "Please select a valid mood."

    try:
        if is_refresh:
            # Get more recommendations than needed for refreshing
            all_recs = get_recommendations_by_mood(mood, num_recommendations=20)

            if all_recs and len(all_recs) > 10:
                # Randomly sample 10 recommendations
                recommendations = random.sample(all_recs, 10)
            else:
                recommendations = all_recs

            if not recommendations:
                return f"No recommendations found for {mood} mood. Please try another mood."

            result_html = f"""
            <div class="recommendation-header">
                <h2>Music for your <span class="highlight">{mood}</span> mood</h2>
                <p class="rec-subheading">Here are some tracks that match your mood:</p>
            </div>
            """
            result_html += '<div class="recommendation-grid">'

            for rec in recommendations:
                image_url = rec.get('image_url') or '/static/default-album.svg'

                result_html += f'''
                <div class="recommendation-card">
                    <div class="album-cover">
                        <img src="{image_url}" alt="Album cover for {rec['track_name']}">
                        <div class="play-overlay">
                            <i class="fas fa-play-circle"></i>
                        </div>
                    </div>
                    <div class="rec-info">
                        <h3 class="song-title">{rec['track_name']}</h3>
                        <p class="artist-name">{rec['artist_name']}</p>
                        <div class="button-group">
                            <a href="{rec['url']}" target="_blank" class="lastfm-link">
                                <i class="fas fa-external-link-alt"></i> Last.fm
                            </a>
                            <button class="add-button" onclick="addToPlaylist('{rec['track_name']}', '{rec['artist_name']}')">
                                <i class="fas fa-plus"></i> Add
                            </button>
                        </div>
                    </div>
                </div>
                '''

            result_html += '</div>'
            return result_html
        else:
            # Normal behavior for initial mood selection
            output = render_mood_recommendations(mood)
            return output
    except Exception as e:
        return f"Error getting mood recommendations: {str(e)}"


@app.route('/search_artist', methods=['POST'])
@login_required
def search_artist():
    data = request.get_json()
    query = data.get('query', '').strip()

    if not query:
        return jsonify({'status': 'error', 'message': 'No search query provided'}), 400

    try:
        # Search for artists using Last.fm
        search_results = network.search_for_artist(query)
        artists = search_results.get_next_page()

        results = []
        for artist in artists[:5]:  # Limit to 5 results
            artist_obj = artist.item

            # Try to get artist image
            artist_image = None
            try:
                artist_image = artist_obj.get_cover_image(size=3)
            except:
                # If direct method fails, try getting from their top album
                try:
                    top_albums = artist_obj.get_top_albums(limit=1)
                    if top_albums and len(top_albums) > 0:
                        album = top_albums[0].item
                        artist_image = album.get_cover_image(size=3)
                except:
                    pass

            results.append({
                'name': artist_obj.get_name(),
                'url': artist_obj.get_url(),
                'image_url': artist_image
            })

        return jsonify({'status': 'success', 'results': results})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/search_song', methods=['POST'])
@login_required
def search_song():
    data = request.get_json()
    query = data.get('query', '').strip()

    if not query:
        return jsonify({'status': 'error', 'message': 'No search query provided'}), 400

    try:
        # Search for tracks using Last.fm
        search_results = network.search_for_track("", query)
        tracks = search_results.get_next_page()

        results = []
        for track in tracks[:5]:  # Limit to 5 results
            track_obj = track.item
            artist_obj = track_obj.get_artist()

            # Try to get album information and cover art
            album_image = None
            try:
                # Get top albums from this artist
                top_albums = artist_obj.get_top_albums(limit=3)
                if top_albums:
                    # Use the first album's image
                    album = top_albums[0].item
                    album_image = album.get_cover_image(size=3)
            except:
                album_image = None

            results.append({
                'track_name': track_obj.get_name(),
                'artist_name': artist_obj.get_name(),
                'url': track_obj.get_url(),
                'image_url': album_image
            })

        return jsonify({'status': 'success', 'results': results})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/update_profile', methods=['POST'])
@login_required
def update_profile():
    data = request.get_json()

    try:
        # Update bio if provided
        if 'bio' in data:
            current_user.bio = data['bio']

        # Update favorite genre if provided
        if 'favorite_genre' in data:
            current_user.favorite_genre = data['favorite_genre']

        # Update favorite artist if provided
        if 'favorite_artist' in data:
            current_user.favorite_artist = data['favorite_artist']
            # No image for manually entered artist
            current_user.favorite_artist_image = None

        # Update favorite song if provided
        if 'favorite_song' in data:
            current_user.favorite_song = data['favorite_song']
            # No image for manually entered song
            current_user.favorite_song_image = None

        db.session.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/update_favorites', methods=['POST'])
@login_required
def update_favorites():
    data = request.get_json()

    try:
        # Update artist if provided
        if data.get('artist'):
            artist = data['artist']
            current_user.favorite_artist = artist['name']
            current_user.favorite_artist_image = artist.get('image_url')

        # Update song if provided
        if data.get('song'):
            song = data['song']
            current_user.favorite_song = f"{song['track_name']} by {song['artist_name']}"
            current_user.favorite_song_image = song.get('image_url')

        # Update genre if provided
        if 'favorite_genre' in data:
            current_user.favorite_genre = data['favorite_genre']

        # Update bio if provided
        if 'bio' in data:
            current_user.bio = data['bio']

        db.session.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/add_to_playlist', methods=['POST'])
@login_required
def add_to_playlist():
    data = request.get_json()

    if not data:
        return jsonify({'status': 'error', 'message': 'No data provided'}), 400

    item = data.get('item', '').strip()

    if not item:
        return jsonify({'status': 'error', 'message': 'No item provided'}), 400

    try:
        # Load existing recent vibes
        if current_user.recent_vibes:
            vibes = json.loads(current_user.recent_vibes)
        else:
            vibes = []

        # Insert the new item
        vibes.insert(0, item)
        current_user.recent_vibes = json.dumps(vibes[:10])  # Keep only last 10 items

        db.session.commit()
        return jsonify({'status': 'success'}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/delete_from_playlist', methods=['POST'])
@login_required
def delete_from_playlist():
    data = request.get_json()
    item = data.get('item', '').strip()

    if not item:
        return jsonify({'status': 'error', 'message': 'No item provided'}), 400

    try:
        if current_user.recent_vibes:
            vibes = json.loads(current_user.recent_vibes)
        else:
            vibes = []

        # Remove only the first occurrence of the item
        if item in vibes:
            vibes.remove(item)

        current_user.recent_vibes = json.dumps(vibes)

        db.session.commit()
        return jsonify({'status': 'success'}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/add_artist_to_playlist', methods=['POST'])
@login_required
def add_artist_to_playlist():
    data = request.get_json()
    item = data.get('item', '').strip()

    if not item:
        return jsonify({'status': 'error', 'message': 'No artist provided'}), 400

    try:
        if current_user.recent_artists:
            artists = json.loads(current_user.recent_artists)
        else:
            artists = []

        artists.insert(0, item)
        current_user.recent_artists = json.dumps(artists[:10])

        db.session.commit()
        return jsonify({'status': 'success'}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/delete_artist_from_playlist', methods=['POST'])
@login_required
def delete_artist_from_playlist():
    data = request.get_json()
    item = data.get('item', '').strip()

    if not item:
        return jsonify({'status': 'error', 'message': 'No artist provided'}), 400

    try:
        if current_user.recent_artists:
            artists = json.loads(current_user.recent_artists)
        else:
            artists = []

        if item in artists:
            artists.remove(item)

        current_user.recent_artists = json.dumps(artists)
        db.session.commit()
        return jsonify({'status': 'success'}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)
