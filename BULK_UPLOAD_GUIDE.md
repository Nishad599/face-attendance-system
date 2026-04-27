# Bulk Upload Students Guide

## Overview
The bulk upload feature allows you to quickly import student details (name, ID, email, joining date) into the system. After uploading their details, you can register their faces one by one through the normal registration process.

## Steps to Bulk Upload Students

### Step 1: Download the CSV Template
1. Go to **System Administration** → **Bulk Upload Students** section
2. Click **📥 Download CSV Template** button
3. This downloads `student_template.csv` with the correct format

### Step 2: Prepare Your Data
Open the CSV file in Excel or any text editor and fill in your student data:

```csv
student_id,name,email,joining_date
250840325001,John Doe,john.doe@email.com,2025-08-01
250840325002,Jane Smith,jane.smith@email.com,2025-08-01
250840325003,Alex Johnson,alex.johnson@email.com,2025-08-01
```

**Required Fields:**
- `student_id` - Unique student ID (e.g., 250840325001)
- `name` - Full name of the student
- `email` - Email address
- `joining_date` - (Optional) Date in format YYYY-MM-DD, e.g., 2025-08-01

**Important:**
- Do NOT change the header row
- Keep one student per row
- Make sure student IDs are unique (no duplicates in CSV or in database)
- Email format should be valid

### Step 3: Upload the CSV File
1. In the **Bulk Upload Students** section, click on the file input
2. Select your prepared CSV file
3. Click **🚀 Upload Students** button
4. Wait for the operation to complete

### Step 4: View Results
After upload, you'll see:
- ✅ Number of students successfully added
- ⚠️ Number of students skipped (with reasons)
- Error messages (if any)

### Step 5: Register Student Faces
- Successfully uploaded students will now appear in the system
- Go to **Student Registration** to register their faces as usual
- They can pose for multiple photos and complete the face encoding process

## Example CSV Content

```csv
student_id,name,email,joining_date
250840325001,Aashi Chahal,aashi.chahal@example.com,2025-08-01
250840325002,Abhinav Thakare,abhinav.thakare@example.com,2025-08-01
250840325003,Abhishek Khodke,abhishek.khodke@example.com,2025-08-01
250840325004,Aditya Undalkar,aditya.undalkar@example.com,2025-08-01
250840325005,Afrah Mulla,afrah.mulla@example.com,2025-08-01
250840325006,Akash Jujare,akash.jujare@example.com,2025-08-01
```

## Common Errors & Solutions

| Error | Cause | Solution |
|-------|-------|----------|
| "Missing required columns" | CSV columns are wrong | Download template again and use exact column names |
| "Student ID already exists" | Student already in database | Check for duplicates, remove from CSV |
| "Missing required fields" | Empty cells in required columns | Fill in all required fields (student_id, name, email) |
| "Invalid email format" | Email doesn't look valid | Use proper email format: user@domain.com |

## What Happens When You Upload

✅ **What Gets Created:**
- Student record in database
- Entry in attendance tracking system
- Ready for face registration

❌ **What Does NOT Get Created:**
- Face encodings (added during registration)
- Attendance marks (added during marking)
- Student photo directory (created during first face registration)

## Next Steps After Upload

1. **View uploaded students**: Check dashboard to see new student count
2. **Register faces**: Go to Student Registration to enroll biometrics
3. **Verify records**: Check database stats to confirm all students imported
4. **Mark attendance**: Start marking attendance once faces are registered

## Tips for Efficient Bulk Upload

- ✅ Prepare all student data in advance (CSV format)
- ✅ Validate student IDs and emails before upload
- ✅ Upload in batches if you have a large number of students
- ✅ Register faces in groups during dedicated registration sessions
- ✅ Keep a backup of your CSV file

## Troubleshooting

**File won't upload:**
- Ensure file is in CSV format (.csv extension)
- Check file size (should be small, typically < 5MB)
- Try downloading template again and copying your data

**Some students skipped:**
- Check error message for specific reason
- Fix those rows in CSV and re-upload (they won't create duplicates)
- Or add them manually through the registration interface

**Can't find uploaded students:**
- Refresh the dashboard page
- Check "Total Students" in Database Stats
- Go to Student Registration page

---

**Note:** Currently, 3 students have already been manually registered. You can bulk upload additional students and mix them with manually registered students.
