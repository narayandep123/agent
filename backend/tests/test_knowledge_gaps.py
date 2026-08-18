from app.rag import retriever


def test_unknown_policy_question_opens_deduplicated_knowledge_gap(client, student_headers, admin_headers):
    question = "What is the hostel closing time?"
    first = client.post("/api/v1/assistant", json={"text": question}, headers=student_headers).json()
    assert first["type"] == "message"
    assert "don't want to guess" in first["message"]
    assert first["knowledge_gap"]["status"] == "OPEN"

    second = client.post("/api/v1/assistant", json={"text": question}, headers=student_headers).json()
    assert second["knowledge_gap"]["id"] == first["knowledge_gap"]["id"]
    gaps = client.get("/api/v1/admin/knowledge-gaps", headers=admin_headers).json()
    gap = next(row for row in gaps if row["id"] == first["knowledge_gap"]["id"])
    assert gap["occurrences"] == 2


def test_admin_can_publish_policy_for_gap_and_make_it_searchable(client, student_headers, admin_headers, tmp_path):
    question = "What is the campus observatory shuttle departure schedule?"
    gap_id = client.post("/api/v1/assistant", json={"text": question}, headers=student_headers).json()["knowledge_gap"]["id"]
    original_dir = retriever.DOCUMENTS_DIR
    retriever.DOCUMENTS_DIR = tmp_path
    retriever.reload_corpus()
    try:
        response = client.post(f"/api/v1/admin/knowledge-gaps/{gap_id}/policy", headers=admin_headers,
            data={"title": "Zeta Residence Timing Policy", "version": "1.0"},
            files={"policy_file": ("observatory.md", b"The orbital observatory shuttle departs at 10:30 PM every day. Passengers arriving later must contact transport staff.", "text/markdown")})
        assert response.status_code == 200
        assert response.json()["searchable"] is True
        match = retriever.search(question, k=1)[0]
        assert match.policy_id == response.json()["policy_id"]
        assert "10:30 PM" in match.answer
    finally:
        retriever.DOCUMENTS_DIR = original_dir
        retriever.reload_corpus()


def test_knowledge_gap_queue_requires_admin(client, student_headers):
    assert client.get("/api/v1/admin/knowledge-gaps", headers=student_headers).status_code == 403
