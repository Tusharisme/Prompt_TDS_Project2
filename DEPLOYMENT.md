# Deployment Instructions - Hugging Face Spaces

## ✅ Completed
- Code pushed to GitHub: https://github.com/Tusharisme/Prompt_TDS_Project2
- Code pushed to Hugging Face: https://huggingface.co/spaces/Tusharisme/TDS_Project2
- PDF file removed from repository (was blocking HF deployment)

## Next Steps

### 1. Configure Secrets in Hugging Face
**CRITICAL**: Your app will not work until you add these secrets!

Go to: https://huggingface.co/spaces/Tusharisme/TDS_Project2/settings

Under **Repository secrets**, add:
- `GEMINI_API_KEY`: Your Google Gemini API key
- `STUDENT_EMAIL`: `23f2003751@ds.study.iitm.ac.in`
- `STUDENT_SECRET`: `abc1234`
- `CORS_ORIGINS`: `*`
- `APP_ENV`: `production`

### 2. Wait for Build
After adding secrets, HF will rebuild the Docker container. This takes 2-5 minutes. Watch the **Build** tab.

### 3. Get Your API URL
Once built, your API endpoint will be:
```
https://tusharisme-tds-project2.hf.space/quiz
```

### 4. Test the Deployment
```python
import requests
url = "https://tusharisme-tds-project2.hf.space/quiz"
payload = {
    "email": "23f2003751@ds.study.iitm.ac.in",
    "secret": "abc1234",
    "url": "https://tds-llm-analysis.s-anand.net/demo"
}
resp = requests.post(url, json=payload)
print(resp.json())
```

### 5. Submit to Google Form
Use:
- **API Endpoint**: `https://tusharisme-tds-project2.hf.space/quiz`
- **GitHub Repo**: `https://github.com/Tusharisme/Prompt_TDS_Project2`

## Troubleshooting
- **503 Error**: Space is still building or sleeping (free tier).
- **401/403/500**: Check if secrets are set correctly.
- **No response**: Check the HF Space logs tab.
