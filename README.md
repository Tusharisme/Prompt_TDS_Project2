# LLM Analysis Quiz Solver - TDS Project 2

An automated quiz solver built with FastAPI and Google Gemini that can fetch, parse, analyze, and solve data-related quizzes.

## Features

- ✅ FastAPI REST API endpoint accepting quiz tasks
- ✅ Secret-based authentication
- ✅ Automated quiz solving using Google Gemini LLM
- ✅ Support for data analysis questions (CSV, Excel, PDF processing)
- ✅ Background task execution
- ✅ Recursive quiz handling (follows quiz chains)
- ✅ Docker deployment ready

## Project Structure

```
.
├── app/
│   ├── main.py              # FastAPI application & endpoints
│   ├── config.py            # Configuration & environment variables
│   ├── schemas.py           # Pydantic models
│   ├── quiz_solver.py       # Quiz solving logic
│   └── utils/
│       └── llm_client.py    # Google Gemini API client
├── tests/
│   ├── test_api.py          # API endpoint tests
│   └── test_solver.py       # Solver logic tests
├── Dockerfile               # Docker configuration for deployment
├── requirements.txt         # Python dependencies
└── .gitignore              # Git ignore rules
```

## Setup

### 1. Clone the repository
```bash
git clone https://github.com/Tusharisme/Prompt_TDS_Project2.git
cd Prompt_TDS_Project2
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure environment variables
Create a `.env` file with:
```env
STUDENT_EMAIL=your_email@example.com
STUDENT_SECRET=your_secret
GEMINI_API_KEY=your_gemini_api_key
CORS_ORIGINS=*
APP_ENV=production
```

### 4. Run locally
```bash
uvicorn app.main:app --reload
```

API will be available at `http://localhost:8000`

## API Usage

### Endpoint: `POST /quiz`

**Request:**
```json
{
  "email": "your_email@example.com",
  "secret": "your_secret",
  "url": "https://example.com/quiz-123"
}
```

**Response:**
```json
{
  "ok": true,
  "message": "Secret verified. Solver started in background.",
  "echo": {
    "email": "your_email@example.com",
    "secret": "your_secret",
    "url": "https://example.com/quiz-123"
  }
}
```

## Deployment

### Hugging Face Spaces (Docker)

1. Push to Hugging Face Space
2. Configure secrets in Space settings:
   - `GEMINI_API_KEY`
   - `STUDENT_EMAIL`
   - `STUDENT_SECRET`
   - `CORS_ORIGINS`
   - `APP_ENV`
3. Space will auto-build and deploy

Live API: `https://tusharisme-tds-project2.hf.space/quiz`

## How It Works

1. **Receive Request**: API endpoint validates secret and accepts quiz URL
2. **Fetch Quiz**: Solver fetches the HTML page and extracts the question (handles base64-encoded content)
3. **Analyze Question**: Uses Google Gemini to understand the question
4. **Generate Solution**: LLM generates Python code if data analysis is needed, or provides direct answer
5. **Execute Code**: Runs generated pandas/numpy code safely
6. **Submit Answer**: Posts answer to the submission endpoint
7. **Handle Next Quiz**: If response contains new URL, repeats the process

## Technologies Used

- **FastAPI**: Web framework
- **Google Gemini API**: LLM for understanding and solving questions
- **Pydantic**: Data validation
- **BeautifulSoup4**: HTML parsing
- **Pandas/Numpy**: Data analysis
- **Docker**: Containerization
- **Loguru**: Logging

## Testing

Run tests:
```bash
pytest
```

## License

MIT License

## Author

Tushar - IIT Madras (23f2003751@ds.study.iitm.ac.in)
