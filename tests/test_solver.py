import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.quiz_solver import solve_quiz, extract_question

@pytest.mark.asyncio
async def test_solve_quiz_flow():
    # Mock httpx.AsyncClient
    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = mock_client.return_value
        mock_instance.__aenter__.return_value = mock_instance
        
        # Mock responses for:
        # 1. Fetch quiz page
        # 2. Submit answer (correct) -> Next URL
        # 3. Fetch next quiz page
        # 4. Submit answer (correct) -> No URL (Done)
        
        mock_instance.get.side_effect = [
            # Response 1: First Quiz Page
            MagicMock(
                status_code=200,
                text="""<html><body>
                <script>document.write(atob('V2hhdCBpcyAxKzE/'))</script>
                Post your answer to https://example.com/submit
                </body></html>""",
                raise_for_status=lambda: None
            ),
            # Response 3: Second Quiz Page
            MagicMock(
                status_code=200,
                text="""<html><body>Question 2</body></html>""",
                raise_for_status=lambda: None
            )
        ]
        
        mock_instance.post.side_effect = [
            # Response 2: Submission 1 Result
            MagicMock(
                status_code=200,
                json=lambda: {"correct": True, "url": "http://next.com"},
                raise_for_status=lambda: None
            ),
            # Response 4: Submission 2 Result
            MagicMock(
                status_code=200,
                json=lambda: {"correct": True}, # No URL = Done
                raise_for_status=lambda: None
            )
        ]
        
        # Mock LLM to return answers
        with patch("app.quiz_solver.query_llm", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = ["2", "Answer 2"]
            
            await solve_quiz("http://start.com", "test@email.com", "secret")
            
            # Verify flow
            assert mock_instance.get.call_count == 2
            assert mock_instance.post.call_count == 2
            assert mock_llm.call_count == 2

def test_extract_question():
    html = """
    <div id="result"></div>
    <script>
    document.querySelector("#result").innerHTML = atob(`SGVsbG8gV29ybGQ=`);
    </script>
    """
    assert extract_question(html) == "Hello World"
