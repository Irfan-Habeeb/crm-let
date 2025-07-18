
from datetime import datetime

def safe_parse_date(date_str):
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None
from flask import render_template, request, redirect, url_for, flash, session, make_response, jsonify
from app import app, db
from models import Admin, Student, Tutor, Attendance, StudentTutorLink
from datetime import date, datetime, timedelta
from utils import generate_parent_invoice, generate_tutor_invoice
import logging
from functools import wraps
from sqlalchemy import func, extract

logger = logging.getLogger(__name__)

# Admin authentication decorator
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            flash('Please log in to access the admin dashboard.', 'error')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    return redirect(url_for('tutor_login'))

# Admin Authentication Routes (Secret URL)
@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        admin = Admin.query.filter_by(username=username).first()
        
        if admin and admin.check_password(password):
            session['admin_id'] = admin.id
            session['admin_username'] = admin.username
            flash(f'Welcome back, {admin.username}!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid username or password.', 'error')
    
    return render_template('admin_login.html')

@app.route('/admin_logout')
def admin_logout():
    session.pop('admin_id', None)
    session.pop('admin_username', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('admin_login'))


@app.route('/admin')
@admin_required
def admin_dashboard():
    from datetime import datetime, timedelta
    from sqlalchemy import func, extract
    # Get filter parameters
    filter_period = request.args.get('filter_period', 'week')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    # Set date range based on filter
    today = datetime.now().date()
    if filter_period == 'week':
        start_date = today - timedelta(days=7)
        end_date = today
    elif filter_period == 'month':
        start_date = today.replace(day=1)
        end_date = today
    elif filter_period == 'year':
        start_date = today.replace(month=1, day=1)
        end_date = today
    elif filter_period == 'custom' and start_date and end_date:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    else:
        start_date = today - timedelta(days=7)
        end_date = today

    total_students = Student.query.count()
    total_tutors = Tutor.query.count()
    total_attendance = Attendance.query.count()

    # ✅ Revenue: fix broken SQL by calculating in Python
    student_class_counts = (
        db.session.query(
            Student.id,
            Student.per_class_fee,
            func.count(Attendance.id)
        )
        .join(Attendance)
        .filter(Attendance.date.between(start_date, end_date))
        .group_by(Student.id, Student.per_class_fee)
        .all()
    )
    total_revenue = sum(fee * count for _, fee, count in student_class_counts)

    total_payout = db.session.query(
        func.sum(StudentTutorLink.pay_per_class)
    ).select_from(StudentTutorLink).join(Attendance,
        (StudentTutorLink.student_id == Attendance.student_id) &
        (StudentTutorLink.tutor_id == Attendance.tutor_id)
    ).filter(
        Attendance.date.between(start_date, end_date)
    ).scalar() or 0

    net_balance = total_revenue - total_payout

    # Fix top students query - convert to JSON-serializable format
    top_student_results = db.session.query(
        Student.name,
        func.count(Attendance.id).label('class_count')
    ).join(Attendance).filter(
        Attendance.date.between(start_date, end_date)
    ).group_by(Student.id, Student.name).order_by(
        func.count(Attendance.id).desc()
    ).limit(10).all()
    
    # Convert to JSON-serializable format
    top_students = []
    for row in top_student_results:
        top_students.append({
            'name': row.name,
            'class_count': int(row.class_count)
        })

    # Fix monthly earnings query - convert Row objects to dictionaries
    monthly_results = db.session.query(
        extract('month', Attendance.date).label('month'),
        extract('year', Attendance.date).label('year'),
        func.sum(Student.per_class_fee).label('revenue'),
        func.sum(StudentTutorLink.pay_per_class).label('payout')
    ).select_from(Attendance).join(Student).join(StudentTutorLink,
        (StudentTutorLink.student_id == Attendance.student_id) &
        (StudentTutorLink.tutor_id == Attendance.tutor_id)
    ).filter(
        Attendance.date >= (today - timedelta(days=365))
    ).group_by(
        extract('month', Attendance.date),
        extract('year', Attendance.date)
    ).order_by('year', 'month').all()
    
    # Convert to JSON-serializable format
    monthly_earnings = []
    for row in monthly_results:
        monthly_earnings.append({
            'month': int(row.month) if row.month else 1,
            'year': int(row.year) if row.year else datetime.now().year,
            'revenue': float(row.revenue) if row.revenue else 0,
            'payout': float(row.payout) if row.payout else 0
        })

    return render_template('admin_dashboard.html',
        total_students=total_students,
        total_tutors=total_tutors,
        total_attendance=total_attendance,
        total_revenue=total_revenue,
        total_payout=total_payout,
        net_balance=net_balance,
        top_students=top_students,
        monthly_earnings=monthly_earnings,
        filter_period=filter_period,
        start_date=start_date,
        end_date=end_date
    )

# Student Management Routes
@app.route('/admin_students')
@admin_required
def admin_students():
    students = Student.query.all()
    return render_template('admin_students.html', students=students)

# Tutor Management Routes  
@app.route('/admin_tutors')
@admin_required
def admin_tutors():
    tutors = Tutor.query.all()
    return render_template('admin_tutors.html', tutors=tutors)

# Payment Management Routes
@app.route('/admin_payments')
@admin_required  
def admin_payments():
    students = Student.query.all()
    tutors = Tutor.query.all()
    return render_template('admin_payments.html', students=students, tutors=tutors)

# Detail profile routes
@app.route('/student_profile/<int:student_id>')
@admin_required  
def student_profile(student_id):
    student = Student.query.get_or_404(student_id)
    attendance_records = Attendance.query.filter_by(student_id=student_id).order_by(Attendance.date.desc()).limit(20).all()
    return render_template('student_profile.html', student=student, attendance_records=attendance_records)

@app.route('/tutor_profile/<int:tutor_id>')
@admin_required
def tutor_profile(tutor_id):
    tutor = Tutor.query.get_or_404(tutor_id)
    attendance_records = Attendance.query.filter_by(tutor_id=tutor_id).order_by(Attendance.date.desc()).limit(20).all()
    return render_template('tutor_profile.html', tutor=tutor, attendance_records=attendance_records)
@admin_required
def admin_payments():
    tab = request.args.get('tab', 'students')
    
    if tab == 'students':
        students = Student.query.all()
        return render_template('admin_payments.html', students=students, tab=tab)
    else:
        tutors = Tutor.query.all()
        return render_template('admin_payments.html', tutors=tutors, tab=tab)

@app.route('/admin/update_payment_status', methods=['POST'])
@admin_required
def update_payment_status():
    entity_type = request.form['entity_type']
    entity_id = request.form['entity_id']
    status = request.form['status']
    payment_date = request.form.get('payment_date')
    
    if entity_type == 'student':
        student = Student.query.get_or_404(entity_id)
        student.payment_status = status
        if payment_date:
            student.last_payment_date = datetime.strptime(payment_date, '%Y-%m-%d').date()
        db.session.commit()
        flash('Student payment status updated successfully!', 'success')
    else:
        tutor = Tutor.query.get_or_404(entity_id)
        tutor.payment_status = status
        if payment_date:
            tutor.last_payment_date = datetime.strptime(payment_date, '%Y-%m-%d').date()
        db.session.commit()
        flash('Tutor payment status updated successfully!', 'success')
    
    return redirect(url_for('admin_payments', tab=entity_type + 's'))

# Student Management Routes
@app.route('/add_student', methods=['GET', 'POST'])
@admin_required
def add_student():
    if request.method == 'POST':
        try:
            name = request.form['name']
            parent_name = request.form['parent_name']
            class_level = request.form['class_level']
            per_class_fee = int(request.form['per_class_fee'])
            subjects = request.form['subjects']
            
            # Get selected tutors and their pay rates
            selected_tutors = request.form.getlist('selected_tutors')
            tutor_pay_rates = {}
            for tutor_id in selected_tutors:
                pay_rate = request.form.get(f'pay_rate_{tutor_id}')
                if pay_rate:
                    tutor_pay_rates[int(tutor_id)] = int(pay_rate)
            
            student = Student(
                name=name,
                parent_name=parent_name,
                class_level=class_level,
                per_class_fee=per_class_fee,
                subjects=subjects
            )
            
            db.session.add(student)
            db.session.flush()  # To get student ID
            
            # Create tutor links
            for tutor_id, pay_rate in tutor_pay_rates.items():
                link = StudentTutorLink(
                    student_id=student.id,
                    tutor_id=tutor_id,
                    pay_per_class=pay_rate
                )
                db.session.add(link)
            
            db.session.commit()
            flash('Student added successfully!', 'success')
            return redirect(url_for('admin_students'))
        except Exception as e:
            logger.error(f"Error adding student: {e}")
            flash('Error adding student. Please try again.', 'error')
    
    tutors = Tutor.query.all()
    return render_template('add_student.html', tutors=tutors)

@app.route('/edit_student/<int:student_id>', methods=['GET', 'POST'])
@admin_required
def edit_student(student_id):
    student = Student.query.get_or_404(student_id)
    
    if request.method == 'POST':
        try:
            student.name = request.form['name']
            student.parent_name = request.form['parent_name']
            student.class_level = request.form['class_level']
            student.per_class_fee = int(request.form['per_class_fee'])
            student.subjects = request.form['subjects']
            
            # Get selected tutors and their pay rates
            selected_tutors = request.form.getlist('selected_tutors')
            tutor_pay_rates = {}
            for tutor_id in selected_tutors:
                pay_rate = request.form.get(f'pay_rate_{tutor_id}')
                if pay_rate:
                    tutor_pay_rates[int(tutor_id)] = int(pay_rate)
            
            # Remove existing tutor links
            StudentTutorLink.query.filter_by(student_id=student.id).delete()
            
            # Create new tutor links
            for tutor_id, pay_rate in tutor_pay_rates.items():
                link = StudentTutorLink(
                    student_id=student.id,
                    tutor_id=tutor_id,
                    pay_per_class=pay_rate
                )
                db.session.add(link)
            
            db.session.commit()
            flash('Student updated successfully!', 'success')
            return redirect(url_for('admin_students'))
        except Exception as e:
            logger.error(f"Error updating student: {e}")
            flash('Error updating student. Please try again.', 'error')
    
    tutors = Tutor.query.all()
    # Get current tutor assignments
    current_links = {link.tutor_id: link.pay_per_class for link in student.tutor_links}
    return render_template('edit_student.html', student=student, tutors=tutors, current_links=current_links)

@app.route('/delete_student/<int:student_id>')
@admin_required
def delete_student(student_id):
    try:
        student = Student.query.get_or_404(student_id)
        # Delete associated attendance records
        Attendance.query.filter_by(student_id=student_id).delete()
        db.session.delete(student)
        db.session.commit()
        flash('Student deleted successfully!', 'success')
    except Exception as e:
        logger.error(f"Error deleting student: {e}")
        flash('Error deleting student. Please try again.', 'error')
    
    return redirect(url_for('admin_students'))

# Tutor Management Routes
@app.route('/generate_tutor_credentials', methods=['POST'])
@admin_required
def generate_tutor_credentials():
    from datetime import datetime
    name = request.form['name']
    dob = request.form['date_of_birth']
    mobile = request.form['mobile_number']
    
    # Parse DOB
    dob_date = datetime.strptime(dob, '%Y-%m-%d').date()
    
    # Generate username: first name + day + month of DOB
    first_name = name.split()[0].lower()
    username = f"{first_name}{dob_date.day:02d}{dob_date.month:02d}"
    
    # Check if username exists and modify if needed
    counter = 1
    original_username = username
    while Tutor.query.filter_by(username=username).first():
        username = f"{original_username}{counter}"
        counter += 1
    
    # Generate password: mobile number + year of birth
    password = f"{mobile}{dob_date.year}"
    
    return jsonify({
        'username': username,
        'password': password
    })

@app.route('/add_tutor', methods=['GET', 'POST'])
@admin_required
def add_tutor():
    if request.method == 'POST':
        try:
            name = request.form['name']
            class_group = request.form['class_group']
            date_of_birth = datetime.strptime(request.form['date_of_birth'], '%Y-%m-%d').date()
            mobile_number = request.form['mobile_number']
            payment_details = request.form.get('payment_details', '')
            username = request.form['username']
            password = request.form['password']
            
            # Check if username already exists
            existing_tutor = Tutor.query.filter_by(username=username).first()
            if existing_tutor:
                flash('Username already exists. Please choose a different username.', 'error')
                return render_template('add_tutor.html')
            
            tutor = Tutor(
                name=name,
                class_group=class_group,
                date_of_birth=date_of_birth,
                mobile_number=mobile_number,
                payment_details=payment_details,
                username=username
            )
            tutor.set_password(password)
            
            db.session.add(tutor)
            db.session.commit()
            flash('Tutor added successfully!', 'success')
            return redirect(url_for('admin_tutors'))
        except Exception as e:
            logger.error(f"Error adding tutor: {e}")
            flash('Error adding tutor. Please try again.', 'error')
    
    return render_template('add_tutor.html')

@app.route('/edit_tutor/<int:tutor_id>', methods=['GET', 'POST'])
@admin_required
def edit_tutor(tutor_id):
    tutor = Tutor.query.get_or_404(tutor_id)
    
    if request.method == 'POST':
        try:
            tutor.name = request.form['name']
            tutor.class_group = request.form['class_group']
            tutor.per_class_pay = int(request.form['per_class_pay'])
            
            # Check if username changed and if new username exists
            new_username = request.form['username']
            if new_username != tutor.username:
                existing_tutor = Tutor.query.filter_by(username=new_username).first()
                if existing_tutor:
                    flash('Username already exists. Please choose a different username.', 'error')
                    return render_template('edit_tutor.html', tutor=tutor)
                tutor.username = new_username
            
            # Update password if provided
            new_password = request.form.get('password')
            if new_password:
                tutor.set_password(new_password)
            
            db.session.commit()
            flash('Tutor updated successfully!', 'success')
            return redirect(url_for('admin_tutors'))
        except Exception as e:
            logger.error(f"Error updating tutor: {e}")
            flash('Error updating tutor. Please try again.', 'error')
    
    return render_template('edit_tutor.html', tutor=tutor)

@app.route('/delete_tutor/<int:tutor_id>')
@admin_required
def delete_tutor(tutor_id):
    try:
        tutor = Tutor.query.get_or_404(tutor_id)
        # Delete tutor links with students
        StudentTutorLink.query.filter_by(tutor_id=tutor_id).delete()
        # Delete associated attendance records
        Attendance.query.filter_by(tutor_id=tutor_id).delete()
        db.session.delete(tutor)
        db.session.commit()
        flash('Tutor deleted successfully!', 'success')
    except Exception as e:
        logger.error(f"Error deleting tutor: {e}")
        flash('Error deleting tutor. Please try again.', 'error')
    
    return redirect(url_for('admin_tutors'))

# Tutor Login and Attendance Routes
@app.route('/login', methods=['GET', 'POST'])
@app.route('/tutor_login', methods=['GET', 'POST'])
def tutor_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        tutor = Tutor.query.filter_by(username=username).first()
        
        if tutor and tutor.check_password(password):
            session['tutor_id'] = tutor.id
            session['tutor_name'] = tutor.name
            flash(f'Welcome, {tutor.name}!', 'success')
            return redirect(url_for('tutor_dashboard'))
        else:
            flash('Invalid username or password.', 'error')
    
    return render_template('tutor_login.html')

@app.route('/tutor_logout')
def tutor_logout():
    session.pop('tutor_id', None)
    session.pop('tutor_name', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('tutor_login'))

@app.route('/tutor_dashboard')
def tutor_dashboard():
    if 'tutor_id' not in session:
        flash('Please log in to access this page.', 'error')
        return redirect(url_for('tutor_login'))
    
    from datetime import datetime, timedelta
    
    tutor_id = session['tutor_id']
    tutor = Tutor.query.get_or_404(tutor_id)
    
    # Get filter parameters
    filter_period = request.args.get('filter_period', 'week')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    # Set date range based on filter
    today = datetime.now().date()
    if filter_period == 'day':
        start_date = end_date = today
    elif filter_period == 'week':
        start_date = today - timedelta(days=7)
        end_date = today
    elif filter_period == 'month':
        start_date = today.replace(day=1)
        end_date = today
    elif filter_period == 'custom' and start_date and end_date:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    else:
        start_date = today - timedelta(days=7)
        end_date = today
    
    # Get attendance records with filters
    attendance_query = Attendance.query.filter(
        Attendance.tutor_id == tutor_id,
        Attendance.date.between(start_date, end_date)
    ).order_by(Attendance.created_at.desc())
    
    recent_attendance = attendance_query.limit(10).all()
    
    # Get assigned students through links
    assigned_students = db.session.query(Student).join(StudentTutorLink).filter(
        StudentTutorLink.tutor_id == tutor_id
    ).all()
    
    # Calculate student performance averages
    student_performance = db.session.query(
        Student.name,
        func.avg(Attendance.rating).label('avg_rating'),
        func.count(Attendance.id).label('class_count')
    ).join(Attendance).filter(
        Attendance.tutor_id == tutor_id,
        Attendance.date.between(start_date, end_date)
    ).group_by(Student.id, Student.name).all()
    
    return render_template('tutor_dashboard.html', 
                         tutor=tutor,
                         assigned_students=assigned_students,
                         recent_attendance=recent_attendance,
                         student_performance=student_performance,
                         filter_period=filter_period,
                         start_date=start_date,
                         end_date=end_date)

@app.route('/submit', methods=['GET', 'POST'])
def tutor_submit():
    if 'tutor_id' not in session:
        flash('Please log in to access this page.', 'error')
        return redirect(url_for('tutor_login'))
    
    tutor_id = session['tutor_id']
    tutor = Tutor.query.get_or_404(tutor_id)
    
    # Get assigned students through links
    assigned_students = db.session.query(Student).join(StudentTutorLink).filter(
        StudentTutorLink.tutor_id == tutor_id
    ).all()
    
    if request.method == 'POST':
        try:
            student_id = int(request.form['student_id'])
            subject = request.form['subject']
            attendance_date = datetime.strptime(request.form['date'], '%Y-%m-%d').date()
            start_time = datetime.strptime(request.form['start_time'], '%H:%M').time()
            end_time = datetime.strptime(request.form['end_time'], '%H:%M').time()
            rating = int(request.form['rating'])
            remarks = request.form.get('remarks', '')
            
            attendance = Attendance(
                tutor_id=tutor_id,
                student_id=student_id,
                subject=subject,
                date=attendance_date,
                start_time=start_time,
                end_time=end_time,
                rating=rating,
                remarks=remarks
            )
            
            db.session.add(attendance)
            db.session.commit()
            flash('Attendance submitted successfully!', 'success')
            return redirect(url_for('tutor_dashboard'))
        except Exception as e:
            logger.error(f"Error submitting attendance: {e}")
            flash('Error submitting attendance. Please try again.', 'error')
    
    return render_template('tutor_submit.html', 
                         tutor=tutor, 
                         students=assigned_students,
                         today=date.today().strftime('%Y-%m-%d'))

# AJAX route to get student subjects
@app.route('/get_student_subjects/<int:student_id>')
def get_student_subjects(student_id):
    student = Student.query.get_or_404(student_id)
    subjects = student.get_subjects_list()
    return jsonify({'subjects': subjects})

# Student Profile Route
@app.route('/student_profile/<int:student_id>')
@admin_required
def student_profile(student_id):
    student = Student.query.get_or_404(student_id)
    
    # Filter parameters
    subject_filter = request.args.get('subject_filter', '')
    month_filter = request.args.get('month_filter', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    
    # Build attendance query
    attendance_query = Attendance.query.filter_by(student_id=student_id)
    
    if subject_filter:
        attendance_query = attendance_query.filter(Attendance.subject == subject_filter)
    
    if month_filter:
        year, month = month_filter.split('-')
        from datetime import datetime
        start_month = datetime(int(year), int(month), 1).date()
        if int(month) == 12:
            end_month = datetime(int(year) + 1, 1, 1).date()
        else:
            end_month = datetime(int(year), int(month) + 1, 1).date()
        attendance_query = attendance_query.filter(
            Attendance.date >= start_month,
            Attendance.date < end_month
        )
    elif safe_parse_date(start_date) and safe_parse_date(end_date):
        start = safe_parse_date(start_date)
        end = safe_parse_date(end_date)
        attendance_query = attendance_query.filter(
            Attendance.date >= safe_parse_date(start_date),
            Attendance.date <= safe_parse_date(end_date)
        )
    
    attendance_records = attendance_query.order_by(Attendance.date.desc()).all()
    
    # Group by subject
    subject_attendance = {}
    for record in attendance_records:
        if record.subject not in subject_attendance:
            subject_attendance[record.subject] = []
        subject_attendance[record.subject].append(record)
    
    # Get available subjects and months for filters
    all_subjects = db.session.query(Attendance.subject).filter_by(student_id=student_id).distinct().all()
    available_subjects = [s[0] for s in all_subjects]
    
    return render_template('student_profile.html',
                         student=student,
                         subject_attendance=subject_attendance,
                         attendance_records=attendance_records,
                         available_subjects=available_subjects,
                         subject_filter=subject_filter,
                         month_filter=month_filter,
                         start_date=start_date,
                         end_date=end_date)

# Tutor Profile Route
@app.route('/tutor_profile/<int:tutor_id>')
@admin_required
def tutor_profile(tutor_id):
    tutor = Tutor.query.get_or_404(tutor_id)
    
    # Filter parameters
    subject_filter = request.args.get('subject_filter', '')
    month_filter = request.args.get('month_filter', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    
    # Build attendance query
    attendance_query = db.session.query(Attendance, Student).join(
        Student, Attendance.student_id == Student.id
    ).filter(Attendance.tutor_id == tutor_id)
    
    if subject_filter:
        attendance_query = attendance_query.filter(Attendance.subject == subject_filter)
    
    if month_filter:
        year, month = month_filter.split('-')
        from datetime import datetime
        start_month = datetime(int(year), int(month), 1).date()
        if int(month) == 12:
            end_month = datetime(int(year) + 1, 1, 1).date()
        else:
            end_month = datetime(int(year), int(month) + 1, 1).date()
        attendance_query = attendance_query.filter(
            Attendance.date >= start_month,
            Attendance.date < end_month
        )
    elif safe_parse_date(start_date) and safe_parse_date(end_date):
        start = safe_parse_date(start_date)
        end = safe_parse_date(end_date)
        attendance_query = attendance_query.filter(
            Attendance.date >= safe_parse_date(start_date),
            Attendance.date <= safe_parse_date(end_date)
        )
    
    attendance_records = attendance_query.order_by(Attendance.date.desc()).all()
    
    # Get available subjects for filters
    all_subjects = db.session.query(Attendance.subject).filter_by(tutor_id=tutor_id).distinct().all()
    available_subjects = [s[0] for s in all_subjects]
    
    return render_template('tutor_profile.html',
                         tutor=tutor,
                         attendance_records=attendance_records,
                         available_subjects=available_subjects,
                         subject_filter=subject_filter,
                         month_filter=month_filter,
                         start_date=start_date,
                         end_date=end_date)

# Attendance Management Routes
@app.route('/view_attendance')
@admin_required
def view_attendance():
    attendance_records = db.session.query(Attendance, Student, Tutor).join(
        Student, Attendance.student_id == Student.id
    ).join(
        Tutor, Attendance.tutor_id == Tutor.id
    ).order_by(Attendance.date.desc()).all()
    
    return render_template('view_attendance.html', attendance_records=attendance_records)

@app.route('/delete_attendance/<int:attendance_id>')
def delete_attendance(attendance_id):
    # Check if user is admin or the tutor who created this attendance
    attendance = Attendance.query.get_or_404(attendance_id)
    
    is_admin = 'admin_id' in session
    is_tutor = 'tutor_id' in session and session['tutor_id'] == attendance.tutor_id
    
    if not (is_admin or is_tutor):
        flash('You do not have permission to delete this attendance record.', 'error')
        return redirect(url_for('tutor_login'))
    
    try:
        db.session.delete(attendance)
        db.session.commit()
        flash('Attendance record deleted successfully!', 'success')
    except Exception as e:
        logger.error(f"Error deleting attendance: {e}")
        flash('Error deleting attendance record. Please try again.', 'error')
    
    if is_admin:
        return redirect(url_for('view_attendance'))
    else:
        return redirect(url_for('tutor_dashboard'))

@app.route('/delete_all_attendance/<int:tutor_id>')
@admin_required
def delete_all_attendance(tutor_id):
    try:
        Attendance.query.filter_by(tutor_id=tutor_id).delete()
        db.session.commit()
        flash('All attendance records for this tutor deleted successfully!', 'success')
    except Exception as e:
        logger.error(f"Error deleting all attendance: {e}")
        flash('Error deleting attendance records. Please try again.', 'error')
    
    return redirect(url_for('view_attendance'))

# PDF Invoice Routes
@app.route('/generate_parent_invoice/<int:student_id>')
@admin_required
def handle_generate_parent_invoice(student_id):
    try:
        student = Student.query.get_or_404(student_id)
        
        # Get filter parameters for filtered invoice
        subject_filter = request.args.get('subject_filter', '')
        month_filter = request.args.get('month_filter', '')
        start_date = request.args.get('start_date', '')
        end_date = request.args.get('end_date', '')
        
        pdf_buffer = generate_parent_invoice(student, subject_filter, month_filter, start_date, end_date)
        
        response = make_response(pdf_buffer.getvalue())
        response.headers['Content-Type'] = 'application/pdf'
        
        # Add filter info to filename
        filename_suffix = ""
        if month_filter:
            filename_suffix = f"_{month_filter}"
        elif start_date and end_date:
            filename_suffix = f"_{start_date}_to_{end_date}"
        
        response.headers['Content-Disposition'] = f'attachment; filename=parent_invoice_{student.name.replace(" ", "_")}{filename_suffix}.pdf'
        
        return response
    except Exception as e:
        logger.error(f"Error generating parent invoice: {e}")
        flash('Error generating invoice. Please try again.', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/generate_tutor_invoice/<int:tutor_id>')
@admin_required
def handle_generate_tutor_invoice(tutor_id):
    try:
        tutor = Tutor.query.get_or_404(tutor_id)
        
        # Get filter parameters for filtered invoice
        subject_filter = request.args.get('subject_filter', '')
        month_filter = request.args.get('month_filter', '')
        start_date = request.args.get('start_date', '')
        end_date = request.args.get('end_date', '')
        
        pdf_buffer = generate_tutor_invoice(tutor, subject_filter, month_filter, start_date, end_date)
        
        response = make_response(pdf_buffer.getvalue())
        response.headers['Content-Type'] = 'application/pdf'
        
        # Add filter info to filename
        filename_suffix = ""
        if month_filter:
            filename_suffix = f"_{month_filter}"
        elif start_date and end_date:
            filename_suffix = f"_{start_date}_to_{end_date}"
        
        response.headers['Content-Disposition'] = f'attachment; filename=tutor_invoice_{tutor.name.replace(" ", "_")}{filename_suffix}.pdf'
        
        return response
    except Exception as e:
        logger.error(f"Error generating tutor invoice: {e}")
        flash('Error generating invoice. Please try again.', 'error')
        return redirect(url_for('admin_dashboard'))
