import os
import base64
import cv2
import numpy as np
import openpyxl
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
import face_recognition

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "visionpass_attendance_secret_2026")
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# User model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# Paths and global state
attendance_file = "attendance.xlsx"
images_dir = 'known_faces'
os.makedirs(images_dir, exist_ok=True)

known_face_encodings = []
known_face_names = []

def load_known_faces():
    """Load and encode all reference faces in the known_faces directory."""
    global known_face_encodings, known_face_names
    known_face_encodings = []
    known_face_names = []

    if not os.path.exists(images_dir):
        return

    valid_extensions = ('.jpg', '.jpeg', '.png', '.webp')
    for image_name in os.listdir(images_dir):
        if not image_name.lower().endswith(valid_extensions):
            continue

        image_path = os.path.join(images_dir, image_name)
        try:
            image = face_recognition.load_image_file(image_path)
            encodings = face_recognition.face_encodings(image)
            if len(encodings) > 0:
                known_face_encodings.append(encodings[0])
                # Username is the file name without extension
                name = os.path.splitext(image_name)[0]
                known_face_names.append(name)
                print(f"[Loaded Face] Identity: {name}")
            else:
                print(f"[Warning] No detectable face found in {image_name}")
        except Exception as e:
            print(f"[Error] Failed to process {image_name}: {e}")

# Initialize known faces
load_known_faces()

# Initialize Excel file for attendance if not existing
def init_attendance_file():
    if not os.path.exists(attendance_file):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Attendance"
        ws.append(["Name", "Date", "Time"])
        wb.save(attendance_file)

init_attendance_file()

def get_user_attendance(username):
    """Retrieve attendance records for a specific user from attendance.xlsx."""
    records = []
    if not os.path.exists(attendance_file):
        return records

    try:
        wb = openpyxl.load_workbook(attendance_file, data_only=True)
        ws = wb.active
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row and len(row) >= 3 and row[0]:
                record_name = str(row[0]).strip()
                if record_name.lower() == username.lower():
                    records.append({
                        "name": record_name,
                        "date": str(row[1]) if row[1] else "",
                        "time": str(row[2]) if row[2] else ""
                    })
    except Exception as e:
        print(f"[Error] Reading attendance.xlsx: {e}")

    # Return newest first
    return list(reversed(records))

def record_attendance(name):
    """Append a verified attendance record into attendance.xlsx."""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")

    wb = openpyxl.load_workbook(attendance_file)
    ws = wb.active
    ws.append([name, date_str, time_str])
    wb.save(attendance_file)
    return date_str, time_str

def process_face_image(rgb_image, logged_in_username):
    """Core face matching logic across loaded known faces."""
    face_locations = face_recognition.face_locations(rgb_image)
    if not face_locations:
        return False, "No face detected in the frame. Please look directly at the camera with clear lighting."

    face_encodings = face_recognition.face_encodings(rgb_image, face_locations)
    if not face_encodings:
        return False, "Could not extract facial biometric features. Please try again."

    if not known_face_encodings:
        return False, "No enrolled faces registered in the system database yet."

    for face_encoding in face_encodings:
        matches = face_recognition.compare_faces(known_face_encodings, face_encoding, tolerance=0.52)
        face_distances = face_recognition.face_distance(known_face_encodings, face_encoding)
        best_match_index = int(np.argmin(face_distances))

        if matches[best_match_index]:
            recognized_name = known_face_names[best_match_index]

            if recognized_name.lower() == logged_in_username.lower():
                date_str, time_str = record_attendance(recognized_name)
                return True, f"Attendance successfully marked for {recognized_name} on {date_str} at {time_str}!"
            else:
                return False, f"Face detected as '{recognized_name}', which does NOT match the logged-in user '{logged_in_username}'."

    return False, "Face not recognized. Please ensure your reference face photo is enrolled."

@app.route("/")
def home():
    if "username" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("Username and password are required.", "error")
            return redirect(url_for("register"))

        if User.query.filter_by(username=username).first():
            flash("Username already exists. Please choose a different one.", "error")
            return redirect(url_for("register"))

        # Strictly enforce Face Biometric Capture (required)
        file = request.files.get("face_image")
        webcam_data = request.form.get("webcam_face_data")

        has_webcam = bool(webcam_data and "base64," in webcam_data)
        has_file = bool(file and file.filename != "")

        if not has_webcam and not has_file:
            flash("Face Biometric Capture is required. Please capture a camera snapshot or upload a face photo.", "error")
            return redirect(url_for("register"))

        # Process and verify the face biometric data BEFORE creating the user
        target_path = None
        face_verified = False

        try:
            if has_webcam:
                base64_str = webcam_data.split("base64,")[1]
                img_bytes = base64.b64decode(base64_str)
                target_filename = f"{secure_filename(username)}.jpg"
                target_path = os.path.join(images_dir, target_filename)
                with open(target_path, "wb") as f:
                    f.write(img_bytes)

            elif has_file:
                ext = os.path.splitext(file.filename)[1].lower()
                if ext not in ['.jpg', '.jpeg', '.png', '.webp']:
                    flash("Invalid image format. Please upload JPG, PNG, or WEBP.", "error")
                    return redirect(url_for("register"))
                target_filename = f"{secure_filename(username)}{ext}"
                target_path = os.path.join(images_dir, target_filename)
                file.save(target_path)

            # Detect face in the captured/uploaded image
            loaded_img = face_recognition.load_image_file(target_path)
            encs = face_recognition.face_encodings(loaded_img)

            if len(encs) > 0:
                face_verified = True
            else:
                # Remove file if no face detected
                if target_path and os.path.exists(target_path):
                    os.remove(target_path)
                flash("Face verification failed: No detectable face found in the capture. A clear face photo is required to enroll.", "error")
                return redirect(url_for("register"))

        except Exception as e:
            if target_path and os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except Exception:
                    pass
            flash(f"Biometric processing error: {str(e)}", "error")
            return redirect(url_for("register"))

        # Create user account ONLY after face biometric is successfully verified
        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        # Update in-memory face encodings
        load_known_faces()

        flash(f"Registration successful! Biometric profile enrolled for {username}. Please sign in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")

@app.route("/download_attendance")
def download_attendance():
    if "username" not in session:
        return redirect(url_for("login"))
    if os.path.exists(attendance_file):
        return send_file(attendance_file, as_attachment=True, download_name="attendance.xlsx")
    flash("Attendance log file not found.", "error")
    return redirect(url_for("dashboard"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            session["username"] = user.username
            flash(f"Welcome back, {user.username}!", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid credentials. Please verify your username and password.", "error")
            return redirect(url_for("login"))

    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        flash("Please log in to access the dashboard.", "info")
        return redirect(url_for("login"))

    username = session["username"]
    records = get_user_attendance(username)
    has_face = username.lower() in [n.lower() for n in known_face_names]

    return render_template("dashboard.html", username=username, attendance_records=records, has_face=has_face)

@app.route("/mark_attendance", methods=["GET", "POST"])
def mark_attendance():
    if "username" not in session:
        return redirect(url_for("login"))

    username = session["username"]

    if request.method == "POST":
        image_data = request.form.get("image_data")
        mode = request.form.get("mode")

        # 1. Preferred Method: Browser webcam snapshot (base64 image)
        if image_data and "base64," in image_data:
            try:
                base64_str = image_data.split("base64,")[1]
                img_bytes = base64.b64decode(base64_str)
                nparr = np.frombuffer(img_bytes, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                if frame is None:
                    flash("Failed to decode webcam snapshot.", "error")
                    return redirect(url_for("mark_attendance"))

                # Convert BGR to RGB
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                success, message = process_face_image(rgb_frame, username)

                if success:
                    flash(message, "success")
                    return redirect(url_for("dashboard"))
                else:
                    flash(message, "error")
                    return redirect(url_for("mark_attendance"))
            except Exception as e:
                flash(f"Processing error: {str(e)}", "error")
                return redirect(url_for("mark_attendance"))

        # 2. Fallback Method: Server-side camera (OpenCV VideoCapture)
        elif mode == "server":
            video_capture = cv2.VideoCapture(0)

            if not video_capture.isOpened():
                flash("Error: Could not access server webcam hardware.", "error")
                return redirect(url_for("mark_attendance"))

            try:
                # Discard the first few frames to allow camera sensor/exposure auto-adjustment
                for _ in range(5):
                    video_capture.read()

                ret, frame = video_capture.read()
                video_capture.release()

                if not ret or frame is None:
                    flash("Error: Could not capture clear frame from server camera.", "error")
                    return redirect(url_for("mark_attendance"))

                # Resize for fast processing
                small_frame = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

                success, message = process_face_image(rgb_small_frame, username)
                if success:
                    flash(message, "success")
                    return redirect(url_for("dashboard"))
                else:
                    flash(message, "error")
                    return redirect(url_for("mark_attendance"))
            except Exception as e:
                if video_capture.isOpened():
                    video_capture.release()
                flash(f"Camera capture error: {str(e)}", "error")
                return redirect(url_for("mark_attendance"))

        else:
            flash("No camera feed data received. Please ensure your camera is enabled.", "error")
            return redirect(url_for("mark_attendance"))

    return render_template("mark_attendance.html", username=username)

@app.route("/logout")
def logout():
    session.pop("username", None)
    flash("You have been signed out.", "info")
    return redirect(url_for("login"))

def seed_default_user():
    """Seed test user from README if not already in database."""
    with app.app_context():
        db.create_all()
        # Seed student / student123
        if not User.query.filter_by(username="student").first():
            u = User(username="student")
            u.set_password("student123")
            db.session.add(u)
            db.session.commit()
            print("[Database] Seeded default test user 'student'")

        # Seed nihar if not present (since nihar.jpg exists in known_faces)
        if not User.query.filter_by(username="nihar").first():
            u = User(username="nihar")
            u.set_password("nihar123")
            db.session.add(u)
            db.session.commit()
            print("[Database] Seeded user 'nihar'")

if __name__ == "__main__":
    seed_default_user()
    print("\n" + "="*50)
    print(" VISIONPASS ATTENDANCE SYSTEM IS LIVE")
    print(" Local URL: http://127.0.0.1:5000")
    print("="*50 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=True)