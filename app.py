from flask import Flask, render_template, request, redirect, url_for, session
import openpyxl
from datetime import datetime
import face_recognition
import cv2
import numpy as np
import os

app = Flask(__name__)
app.secret_key = "your_secret_key"  # Required for session management

# Path to the attendance Excel file
attendance_file = "attendance.xlsx"

# Dummy user database (replace with a real database in production)
users = {
    "student": "student123",
    "staff": "staff123"
}

# Load known faces and their names
known_face_encodings = []
known_face_names = []

# Path to the directory containing images of known people
images_dir = 'known_faces'

# Load known faces
for image_name in os.listdir(images_dir):
    image_path = os.path.join(images_dir, image_name)
    image = face_recognition.load_image_file(image_path)
    face_encoding = face_recognition.face_encodings(image)
    if len(face_encoding) > 0:
        known_face_encodings.append(face_encoding[0])
        known_face_names.append(os.path.splitext(image_name)[0])
    else:
        print(f"No face found in {image_name}")

# Initialize Excel file for attendance
if not os.path.exists(attendance_file):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Attendance"
    ws.append(["Name", "Date", "Time"])
    wb.save(attendance_file)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        if username in users and users[username] == password:
            session["username"] = username
            return redirect(url_for("dashboard"))
        else:
            return "Invalid credentials. Please try again."
    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if "username" in session:
        return render_template("dashboard.html", username=session["username"])
    return redirect(url_for("login"))

@app.route("/mark_attendance", methods=["POST"])
def mark_attendance():
    if "username" in session:
        name = session["username"]
        current_time = datetime.now()

        # Initialize video capture
        video_capture = cv2.VideoCapture(0)

        if not video_capture.isOpened():
            return "Error: Could not access webcam."

        # Capture frame-by-frame
        ret, frame = video_capture.read()

        if not ret or frame is None:
            video_capture.release()
            return "Error: Could not capture frame from webcam."

        try:
            # Resize frame for faster processing
            small_frame = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)

            # Convert the image from BGR color (which OpenCV uses) to RGB color
            rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)

            # Find all the faces and face encodings in the current frame
            face_locations = face_recognition.face_locations(rgb_small_frame)

            if not face_locations:
                video_capture.release()
                return "No face detected in the frame."

            # Encode faces
            face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)

            if not face_encodings:
                video_capture.release()
                return "No face encodings found."

            for face_encoding in face_encodings:
                # See if the face is a match for the known face(s)
                matches = face_recognition.compare_faces(known_face_encodings, face_encoding)
                name = "Unknown"

                # Use the known face with the smallest distance to the new face
                face_distances = face_recognition.face_distance(known_face_encodings, face_encoding)
                best_match_index = np.argmin(face_distances)
                if matches[best_match_index]:
                    name = known_face_names[best_match_index]

                    # If the person is recognized and matches the logged-in user
                    if name == session["username"]:
                        # Load the attendance Excel file
                        wb = openpyxl.load_workbook(attendance_file)
                        ws = wb.active
                        ws.append([name, current_time.strftime("%Y-%m-%d"), current_time.strftime("%H:%M:%S")])
                        wb.save(attendance_file)
                        video_capture.release()
                        return f"Attendance recorded for {name} at {current_time}"
                    else:
                        video_capture.release()
                        return "Face does not match the logged-in user."

            video_capture.release()
            return "No matching face detected."
        except Exception as e:
            video_capture.release()
            return f"An error occurred: {str(e)}"
    return redirect(url_for("login"))
@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(url_for("home"))

if __name__ == "__main__":
    app.run(debug=True)