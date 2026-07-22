"""
Recruiter-Match Agent
----------------------
Scores how well a student's profile fits a job/hackathon, given the
Strategist Agent's breakdown of that posting. Deterministic and explainable
on purpose (no LLM call) - a recruiter-facing fit score should be
reproducible and auditable, not vary run to run.

Score = weighted blend of:
  - Skill overlap (hard + soft skills vs student's listed skills) : 45%
  - Experience tier alignment (junior/mid/senior wording vs years) : 25%
  - Location compatibility (remote/city/hybrid preferences)        : 20%
  - Interest/domain alignment (student interests vs role keywords) : 10%
"""

import re
from typing import Dict, List


def _normalize(items: List[str]) -> List[str]:
    return [re.sub(r"[^a-z0-9+#. ]", "", i.lower().strip()) for i in items if i and i.strip()]


def _skill_overlap_score(required_skills: List[str], student_skills: List[str]) -> Dict:
    req = _normalize(required_skills)
    stu = _normalize(student_skills)

    if not req:
        return {"score": 50.0, "matched": [], "missing": [], "note": "No required skills to compare against."}

    matched, missing = [], []
    for r in req:
        hit = any(r in s or s in r for s in stu)
        (matched if hit else missing).append(r)

    ratio = len(matched) / len(req)
    return {
        "score": round(ratio * 100, 1),
        "matched": matched,
        "missing": missing,
    }


_TIER_KEYWORDS = {
    "intern": 0, "internship": 0, "trainee": 0,
    "junior": 1, "entry": 1, "entry-level": 1, "associate": 1,
    "": 2,  # no explicit tier -> treat as mid
    "mid": 2, "engineer ii": 2,
    "senior": 3, "sr": 3, "lead": 4, "staff": 4, "principal": 5,
}


def _infer_required_tier(job_title: str) -> int:
    title_lower = job_title.lower()
    for kw, tier in sorted(_TIER_KEYWORDS.items(), key=lambda kv: -len(kv[0])):
        if kw and kw in title_lower:
            return tier
    return 2  # default: mid-level


def _experience_score(job_title: str, student_years_experience: float) -> Dict:
    required_tier = _infer_required_tier(job_title)
    tier_to_min_years = {0: 0, 1: 0, 2: 2, 3: 4, 4: 6, 5: 8}
    min_years = tier_to_min_years.get(required_tier, 2)

    if student_years_experience >= min_years:
        # Slight penalty if wildly over-qualified for an internship/junior role
        overshoot = student_years_experience - min_years
        score = 100.0 if overshoot <= 3 or required_tier >= 2 else max(70.0, 100 - (overshoot * 8))
    else:
        gap = min_years - student_years_experience
        score = max(0.0, 100 - (gap * 25))

    return {"score": round(score, 1), "required_tier": required_tier, "min_years_expected": min_years}


def _location_score(job_location: str, student_location_pref: str) -> Dict:
    job_loc = (job_location or "").lower()
    pref = (student_location_pref or "").lower().strip()

    if not pref or pref == "any" or pref == "flexible":
        return {"score": 100.0, "note": "No location constraint set by student."}

    if "remote" in pref:
        score = 100.0 if "remote" in job_loc else (40.0 if "hybrid" in job_loc else 20.0)
    elif pref in job_loc or job_loc in pref:
        score = 100.0
    elif "remote" in job_loc:
        score = 80.0  # remote roles are usually workable regardless of city preference
    else:
        score = 30.0

    return {"score": score}


def _interest_score(job_title: str, job_description: str, student_interests: List[str]) -> Dict:
    if not student_interests:
        return {"score": 60.0, "note": "No stated interests to compare against."}

    haystack = f"{job_title} {job_description}".lower()
    hits = [i for i in student_interests if i.lower().strip() in haystack]
    ratio = len(hits) / len(student_interests)
    return {"score": round(40 + ratio * 60, 1), "matched_interests": hits}


def _verdict(total_score: float) -> str:
    if total_score >= 80:
        return "Strong Fit"
    if total_score >= 60:
        return "Good Fit"
    if total_score >= 40:
        return "Stretch Opportunity"
    return "Not Well Aligned"


def run_recruiter_match_agent(job: Dict, strategist_analysis: Dict, student_profile: Dict) -> Dict:
    """
    job: {"title", "company", "location", ...}  (from Scout Agent)
    strategist_analysis: output of run_strategist_agent (hard_skills, soft_skills, job_description, ...)
    student_profile: {
        "skills": [...],
        "years_experience": float,
        "location_preference": str,   # e.g. "Remote", "Bangalore", "Any"
        "interests": [...],
    }
    """
    required_skills = list(strategist_analysis.get("hard_skills", [])) + list(strategist_analysis.get("soft_skills", []))

    skill_result = _skill_overlap_score(required_skills, student_profile.get("skills", []))
    experience_result = _experience_score(job.get("title", ""), float(student_profile.get("years_experience", 0)))
    location_result = _location_score(job.get("location", ""), student_profile.get("location_preference", ""))
    interest_result = _interest_score(job.get("title", ""), strategist_analysis.get("job_description", ""),
                                       student_profile.get("interests", []))

    weights = {"skills": 0.45, "experience": 0.25, "location": 0.20, "interests": 0.10}
    total = (
        skill_result["score"] * weights["skills"]
        + experience_result["score"] * weights["experience"]
        + location_result["score"] * weights["location"]
        + interest_result["score"] * weights["interests"]
    )
    total = round(total, 1)

    return {
        "fit_score": total,
        "verdict": _verdict(total),
        "breakdown": {
            "skill_overlap": {**skill_result, "weight": weights["skills"]},
            "experience_alignment": {**experience_result, "weight": weights["experience"]},
            "location_compatibility": {**location_result, "weight": weights["location"]},
            "interest_alignment": {**interest_result, "weight": weights["interests"]},
        },
    }


if __name__ == "__main__":
    demo_job = {"title": "Junior Python Developer", "location": "Remote"}
    demo_analysis = {
        "job_description": "Build backend services using Python and REST APIs for a fintech platform.",
        "hard_skills": ["Python", "REST APIs", "SQL", "Git"],
        "soft_skills": ["Communication", "Problem Solving"],
    }
    demo_student = {
        "skills": ["Python", "Django", "Git", "Communication"],
        "years_experience": 1,
        "location_preference": "Remote",
        "interests": ["fintech", "backend"],
    }
    import json
    print(json.dumps(run_recruiter_match_agent(demo_job, demo_analysis, demo_student), indent=2))
