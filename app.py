import os
import json
import random
import uuid
from datetime import datetime
from contextlib import contextmanager
from typing import List, Dict, Any, Optional
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import hashlib
import re
import logging

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Configuration
class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'your_secret_key_change_this_in_production')

    # Database configuration
    DB_HOST = os.environ.get('DB_HOST', 'localhost')
    DB_PORT = int(os.environ.get('DB_PORT', 5432))
    DB_USER = os.environ.get('DB_USER', 'postgres')
    DB_PASSWORD = os.environ.get('DB_PASSWORD', '123456')
    DB_NAME = os.environ.get('DB_NAME', 'cactus')

    # Application configuration
    QUESTIONS_FILE = os.environ.get('QUESTIONS_FILE', 'question/2025-08-02.json')
    QUIZ_PARAMS_FILE = os.environ.get('QUIZ_PARAMS_FILE', 'quiz_parameters.json')

    # Security settings
    MIN_TIME_PER_QUESTION = int(os.environ.get('MIN_TIME_PER_QUESTION', 30))  # seconds
    MAX_QUIZ_TIME = int(os.environ.get('MAX_QUIZ_TIME', 1800))  # 30 minutes


app.config.from_object(Config)


# Database connection pool management
@contextmanager
def get_db_connection():
    """Context manager for database connections with proper error handling."""
    conn = None
    try:
        conn = psycopg2.connect(
            host=app.config['DB_HOST'],
            port=app.config['DB_PORT'],
            user=app.config['DB_USER'],
            password=app.config['DB_PASSWORD'],
            dbname=app.config['DB_NAME'],
            cursor_factory=RealDictCursor,
            connect_timeout=10
        )
        yield conn
    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        if conn:
            conn.close()


def load_json_file(filepath: str) -> Dict[str, Any]:
    """Load and parse JSON file with error handling."""
    try:
        with open(filepath, 'r', encoding='utf-8') as file:
            return json.load(file)
    except FileNotFoundError:
        logger.error(f"File not found: {filepath}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {filepath}: {e}")
        raise


def load_questions() -> List[Dict[str, Any]]:
    """Load questions from JSON file."""
    return load_json_file(app.config['QUESTIONS_FILE'])


def load_quiz_parameters() -> Dict[str, Any]:
    """Load quiz parameters from JSON file."""
    try:
        return load_json_file(app.config['QUIZ_PARAMS_FILE'])
    except FileNotFoundError:
        logger.warning(f"Quiz parameters file not found. Using defaults.")
        return {
            'num_questions': 2,
            'passing_level': 0.7,
            'quiz_title': 'Quiz'
        }


def validate_student_name(name: str) -> bool:
    """Validate student name format."""
    if not name or len(name.strip()) < 2:
        return False
    # Allow letters, spaces, hyphens, and apostrophes
    pattern = r"^[a-zA-Z\s\-']+$"
    return bool(re.match(pattern, name.strip()))


def generate_student_hash(name: str, user_agent: str, ip_address: str) -> str:
    """Generate a unique hash for student identification."""
    combined = f"{name.lower().strip()}_{user_agent}_{ip_address}"
    return hashlib.sha256(combined.encode()).hexdigest()


def check_duplicate_attempt(student_hash: str, session_id: str) -> bool:
    """Check if student has already attempted the quiz recently."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """SELECT COUNT(*) FROM session_info 
                       WHERE student_hash = %s AND session_id != %s 
                       AND submission_time > NOW() - INTERVAL '24 hours'""",
                    (student_hash, session_id)
                )
                result = cursor.fetchone()
                count = result[0] if result else 0
                return count > 0
    except Exception as e:
        logger.error(f"Error checking duplicate attempts: {e}")
        return False


def select_random_questions(questions_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Select random questions based on parameters."""
    params = load_quiz_parameters()
    num_questions = params.get('num_questions', 2)

    if len(questions_list) < num_questions:
        logger.warning(
            f"Not enough questions available. Requested: {num_questions}, Available: {len(questions_list)}")
        return questions_list

    return random.sample(questions_list, num_questions)


def generate_session_id() -> str:
    """Generate a unique session ID."""
    return str(uuid.uuid4())


def create_initial_session_info(session_id: str, student_name: str, student_hash: str,
                                page_load_time: datetime, num_questions: int, passing_level: float):
    """Create initial session info in database immediately when quiz starts."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO session_info 
                       (session_id, student_name, student_hash, page_load_time, submission_time, 
                        num_questions, passing_level, ip_address, user_agent) 
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (session_id, student_name, student_hash, page_load_time, None,  # submission_time is NULL initially
                     num_questions, passing_level, request.remote_addr, request.headers.get('User-Agent'))
                )
                conn.commit()
                logger.info(f"Initial session info created for session_id: {session_id}")
    except Exception as e:
        logger.error(f"Error creating initial session info: {e}")
        raise


def update_session_submission_time(session_id: str):
    """Update the submission time when quiz is completed."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """UPDATE session_info SET submission_time = %s WHERE session_id = %s""",
                    (datetime.now(), session_id)
                )
                conn.commit()
                logger.info(f"Submission time updated for session_id: {session_id}")
    except Exception as e:
        logger.error(f"Error updating submission time: {e}")
        raise


def save_quiz_log(session_id: str, question_number: int, question_data: Dict[str, Any],
                  user_answers: List[str], is_correct: bool,
                  first_modified_time: Optional[str], last_modified_time: Optional[str],
                  copy_paste_attempts: int = 0, tab_switches: int = 0):
    """Save quiz log entry to database."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Convert timestamp strings to datetime objects if they exist
                first_modified_dt = None
                last_modified_dt = None

                if first_modified_time:
                    try:
                        first_modified_dt = datetime.fromisoformat(first_modified_time.replace('Z', '+00:00'))
                    except ValueError:
                        logger.warning(f"Invalid first_modified_time format: {first_modified_time}")

                if last_modified_time:
                    try:
                        last_modified_dt = datetime.fromisoformat(last_modified_time.replace('Z', '+00:00'))
                    except ValueError:
                        logger.warning(f"Invalid last_modified_time format: {last_modified_time}")

                query = """INSERT INTO quiz_log 
                          (session_id, question_number, question_id, question, user_answers, 
                           correct_answers, is_correct, first_modified_time, last_modified_time,
                           copy_paste_attempts, tab_switches) 
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""

                cursor.execute(query, (
                    session_id,
                    question_number,
                    question_data.get('question_id', f"q_{question_number}"),
                    question_data['question'],
                    '|'.join(user_answers) if user_answers else '',
                    '|'.join(question_data['correct_answers']),
                    is_correct,
                    first_modified_dt,
                    last_modified_dt,
                    copy_paste_attempts,
                    tab_switches
                ))
                conn.commit()
                logger.info(f"Quiz log saved for session_id: {session_id}, question: {question_number}")
    except Exception as e:
        logger.error(f"Error saving quiz log: {e}")
        raise


def log_security_event(session_id: str, event_type: str, event_details: Dict[str, Any] = None):
    """Log security events to the security_events table."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Convert event_details to JSON if it's provided
                details_json = json.dumps(event_details) if event_details else None

                cursor.execute(
                    """INSERT INTO security_events (session_id, event_type, event_details, ip_address, user_agent)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (session_id, event_type, details_json, request.remote_addr, request.headers.get('User-Agent'))
                )
                conn.commit()
                logger.info(f"Security event logged: {event_type} for session: {session_id}")
    except Exception as e:
        logger.error(f"Error logging security event: {e}")


@app.route('/')
def index():
    """Landing page with student name input."""
    return render_template('student_login.html')


@app.route('/start_quiz', methods=['POST'])
def start_quiz():
    """Start quiz after student authentication."""
    student_name = request.form.get('student_name', '').strip()

    if not validate_student_name(student_name):
        flash('Please enter a valid name (at least 2 characters, letters only).', 'error')
        return redirect(url_for('index'))

    # Clear any existing session data to prevent conflicts
    session.clear()

    # Generate student hash for identification
    user_agent = request.headers.get('User-Agent', '')
    ip_address = request.remote_addr or 'unknown'
    student_hash = generate_student_hash(student_name, user_agent, ip_address)

    # Generate NEW session ID for each quiz attempt
    session_id = generate_session_id()

    # Check for duplicate attempts (optional - remove if students can retake)
    # if check_duplicate_attempt(student_hash, session_id):
    #     flash('You have already taken this quiz recently. Please contact your instructor.', 'warning')
    #     return redirect(url_for('index'))

    # Load questions and parameters
    try:
        questions = load_questions()
        params = load_quiz_parameters()
    except Exception as e:
        logger.error(f"Error loading quiz data: {e}")
        flash('Error loading quiz. Please try again later.', 'error')
        return redirect(url_for('index'))

    # Select random questions
    selected_questions = select_random_questions(questions)

    # Get current time for page load
    page_load_time = datetime.now()

    # STEP 1: Create session info in database FIRST (before any security logging)
    try:
        create_initial_session_info(
            session_id,
            student_name,
            student_hash,
            page_load_time,
            params.get('num_questions', 2),
            params.get('passing_level', 0.7)
        )
    except Exception as e:
        logger.error(f"Error creating session info: {e}")
        flash('Error starting quiz. Please try again later.', 'error')
        return redirect(url_for('index'))

    # STEP 2: Now store in session for frontend use
    session['current_questions'] = selected_questions
    session['session_id'] = session_id
    session['student_name'] = student_name
    session['student_hash'] = student_hash
    session['page_load_time'] = page_load_time.isoformat()
    session['copy_paste_attempts'] = 0
    session['tab_switches'] = 0

    logger.info(f"Quiz started for student: {student_name}, session: {session_id}")

    # STEP 3: Now you can safely log the quiz start event
    try:
        log_security_event(session_id, 'QUIZ_STARTED', {
            'student_name': student_name,
            'num_questions': len(selected_questions),
            'start_time': page_load_time.isoformat()
        })
    except Exception as e:
        # Don't fail the quiz start if security logging fails
        logger.error(f"Error logging quiz start event: {e}")

    return render_template('quiz.html',
                           questions=selected_questions,
                           num_questions=params.get('num_questions', 2),
                           quiz_title=params.get('quiz_title', 'Quiz'),
                           student_name=student_name,
                           max_time=app.config['MAX_QUIZ_TIME'])


@app.route('/submit', methods=['POST'])
def submit():
    """Handle quiz submission and calculate results."""
    try:
        # Check if quiz already submitted for this session
        session_id = session.get('session_id')
        if not session_id or session.get('quiz_submitted', False):
            logger.warning(
                f"Invalid submission attempt - session_id: {session_id}, already_submitted: {session.get('quiz_submitted', False)}")
            flash('Invalid submission. Please start a new quiz.', 'warning')
            return redirect(url_for('index'))

        # Mark quiz as submitted to prevent duplicates
        session['quiz_submitted'] = True
        # Load parameters
        params = load_quiz_parameters()
        passing_level = params.get('passing_level', 0.7)
        num_questions = params.get('num_questions', 2)

        # Get session data
        selected_questions = session.get('current_questions', [])
        session_id = session.get('session_id')
        student_name = session.get('student_name')
        student_hash = session.get('student_hash')
        page_load_time_str = session.get('page_load_time')
        copy_paste_attempts = session.get('copy_paste_attempts', 0)
        tab_switches = session.get('tab_switches', 0)

        if not all([selected_questions, session_id, student_name, page_load_time_str]):
            flash('Session expired. Please start the quiz again.', 'error')
            return redirect(url_for('index'))

        page_load_time = datetime.fromisoformat(page_load_time_str)

        # Check minimum time requirement
        time_elapsed = (datetime.now() - page_load_time).total_seconds()
        min_required_time = len(selected_questions) * app.config['MIN_TIME_PER_QUESTION']

        # Update submission time in session_info
        update_session_submission_time(session_id)

        # Process answers
        score = 0
        results = []
        suspicious_activity = copy_paste_attempts > 5 or tab_switches > 10

        for question_number, question in enumerate(selected_questions, 1):
            # Handle different ways the form data might be structured
            question_key = question.get('question_id', question['question'])
            user_answers = request.form.getlist(question_key)

            # If no answers found with question_id, try with question text
            if not user_answers:
                user_answers = request.form.getlist(question['question'])

            correct_answers = question['correct_answers']

            # Check if answer is correct
            is_correct = (set(user_answers) == set(correct_answers) and
                          len(user_answers) == len(correct_answers))

            # Get timing data
            first_modified_time = request.form.get(f"first_modified_{question.get('question_id', question_number)}")
            last_modified_time = request.form.get(f"last_modified_{question.get('question_id', question_number)}")

            # Save to database
            save_quiz_log(session_id, question_number, question, user_answers,
                          is_correct, first_modified_time, last_modified_time,
                          copy_paste_attempts, tab_switches)

            # Add to results
            results.append({
                'question_id': question.get('question_id', f"q_{question_number}"),
                'question': question['question'],
                'user_answers': user_answers,
                'correct_answers': correct_answers,
                'is_correct': is_correct,
            })

            if is_correct:
                score += 1

        # Log suspicious activity if detected
        if suspicious_activity:
            log_security_event(session_id, 'SUSPICIOUS_ACTIVITY', {
                'copy_paste_attempts': copy_paste_attempts,
                'tab_switches': tab_switches,
                'student_name': student_name,
                'threshold_exceeded': {
                    'copy_paste': copy_paste_attempts > 5,
                    'tab_switches': tab_switches > 10
                }
            })

        # Log quiz completion
        log_security_event(session_id, 'QUIZ_COMPLETED', {
            'student_name': student_name,
            'score': score,
            'total_questions': len(selected_questions),
            'completion_time': datetime.now().isoformat(),
            'time_elapsed_seconds': time_elapsed
        })

        logger.info(
            f"Quiz submitted by {student_name}, session: {session_id}, score: {score}/{len(selected_questions)}")

        # Clear session after successful submission to prevent reuse
        session.clear()

        return render_template('result.html',
                               score=score,
                               total=len(selected_questions),
                               results=results,
                               passing_level=passing_level,
                               selected_questions=selected_questions,
                               student_name=student_name,
                               suspicious_activity=suspicious_activity,
                               copy_paste_attempts=copy_paste_attempts,
                               tab_switches=tab_switches)

    except Exception as e:
        logger.error(f"Error in submit route: {e}")
        flash('An error occurred while processing your submission. Please try again.', 'error')
        return redirect(url_for('index'))


@app.route('/log_activity', methods=['POST'])
def log_activity():
    """Log suspicious activities like copy-paste attempts and tab switches."""
    if 'session_id' not in session:
        return jsonify({'status': 'error', 'message': 'No active session'})

    try:
        activity_type = request.json.get('type')
        session_id = session.get('session_id')

        # Check if session exists in database before logging
        if not session_id:
            return jsonify({'status': 'error', 'message': 'No session ID'})

        if activity_type == 'copy_paste':
            session['copy_paste_attempts'] = session.get('copy_paste_attempts', 0) + 1
            log_security_event(session_id, 'COPY_PASTE_ATTEMPT', {
                'total_attempts': session['copy_paste_attempts'],
                'timestamp': datetime.now().isoformat()
            })
        elif activity_type == 'tab_switch':
            session['tab_switches'] = session.get('tab_switches', 0) + 1
            log_security_event(session_id, 'TAB_SWITCH', {
                'total_switches': session['tab_switches'],
                'timestamp': datetime.now().isoformat()
            })

        return jsonify({'status': 'logged'})
    except Exception as e:
        logger.error(f"Error logging activity: {e}")
        return jsonify({'status': 'error', 'message': 'Failed to log activity'})


@app.route('/health')
def health_check():
    """Health check endpoint."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
        return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()}), 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    try:
        return render_template('error.html', error="Internal server error"), 500
    except:
        # Fallback if error.html doesn't exist
        return '''
        <html>
        <head><title>Error</title></head>
        <body>
            <h1>Internal Server Error</h1>
            <p>An unexpected error occurred. Please try again later.</p>
            <a href="/">Back to Home</a>
        </body>
        </html>
        ''', 500


@app.errorhandler(404)
def not_found_error(error):
    try:
        return render_template('error.html', error="Page not found"), 404
    except:
        # Fallback if error.html doesn't exist
        return '''
        <html>
        <head><title>Page Not Found</title></head>
        <body>
            <h1>Page Not Found</h1>
            <p>The page you are looking for does not exist.</p>
            <a href="/">Back to Home</a>
        </body>
        </html>
        ''', 404


if __name__ == '__main__':
    # Test database connection on startup
    try:
        with get_db_connection() as conn:
            logger.info("Database connection successful")
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")

    app.run(debug=True, host='0.0.0.0', port=5000)
