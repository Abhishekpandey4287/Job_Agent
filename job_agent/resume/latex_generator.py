"""LaTeX resume generator using the user's professional Android template."""

import re
import shutil
import subprocess
from pathlib import Path

from job_agent.config import get_settings
from job_agent.logging_config import get_logger
from job_agent.schemas import JobMatch, MasterResume, TailoredResume

logger = get_logger(__name__)

_DATA_RESUME = Path(__file__).resolve().parent.parent.parent / "data" / "resume"
MASTER_TEMPLATE = _DATA_RESUME / "master_android.tex"
PROFILES_DIR = _DATA_RESUME / "profiles"


def default_master_template() -> Path:
    """Resolve a template that actually exists.

    The flat data/resume/master_android.tex predates the profile folders, so fall
    back to the highest-priority profile's master.tex when it is absent.
    """
    if MASTER_TEMPLATE.exists():
        return MASTER_TEMPLATE
    for candidate in sorted(PROFILES_DIR.glob("*/master.tex")):
        return candidate
    return MASTER_TEMPLATE


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w\-]", "_", name)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:60]


def _fix_unicode_dashes(tex: str) -> str:
    """XeTeX + T1 helvet drops literal en/em dashes; use the T1 commands."""
    if "\\textemdash" in tex or "\\textendash" in tex:
        return tex
    return tex.replace("—", "\\textemdash{}").replace("–", "\\textendash{}")


def _prepare_tex_for_tectonic(tex: str) -> str:
    """Make generated .tex compile under Tectonic (XeTeX).

    Legacy masters used pdfTeX-only \\input{glyphtounicode} / \\pdfgentounicode.
    """
    tex = _fix_unicode_dashes(tex)
    if "\\ifPDFTeX" in tex and "glyphtounicode" in tex:
        return tex

    guarded = (
        "\\usepackage{iftex}\n"
        "\\ifPDFTeX\n"
        "  \\input{glyphtounicode}\n"
        "  \\pdfgentounicode=1\n"
        "\\fi"
    )
    pair = "\\input{glyphtounicode}\n\\pdfgentounicode=1"
    if pair in tex:
        return tex.replace(pair, guarded, 1)

    # Line-by-line fallback for odd spacing
    lines = []
    skip_next_pdfgen = False
    for line in tex.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == "\\input{glyphtounicode}":
            lines.append(guarded + ("\n" if line.endswith("\n") else ""))
            skip_next_pdfgen = True
            continue
        if skip_next_pdfgen and stripped.startswith("\\pdfgentounicode"):
            skip_next_pdfgen = False
            continue
        if stripped.startswith("\\pdfgentounicode"):
            continue
        lines.append(line)
    return "".join(lines)


def _escape_latex(text: str) -> str:
    replacements = {
        "&": r"\&", "%": r"\%", "#": r"\#",
        "_": r"\_", "{": r"\{", "}": r"\}",
    }
    for char, escaped in replacements.items():
        text = text.replace(char, escaped)
    return text


_BN_TOKEN = "<<<BN{}>>>"


def normalize_profile_highlights(text: str) -> str:
    """Turn LLM highlight syntax into \\bn{keyword} without breaking JSON/markdown."""
    text = text or ""
    # Repair previously escaped commands
    text = text.replace(r"\bn\{", r"\bn{").replace(r"\}", "}")
    text = re.sub(r"\\bn\{([^{}]+)\}", lambda m: f"**{m.group(1)}**", text)
    text = re.sub(r"\\b([^\\]+?)\\b", lambda m: f"**{m.group(1).strip()}**", text)
    return text


def _plain_to_latex_profile(text: str) -> str:
    """Wrap profile text in LaTeX, preserving \\bn{...} bold commands."""
    text = normalize_profile_highlights(text).strip()
    if text.startswith("{\\small"):
        return text

    placeholders: list[str] = []

    def _stash(match: re.Match) -> str:
        placeholders.append(match.group(1).strip())
        return _BN_TOKEN.format(len(placeholders) - 1)

    stashed = re.sub(r"\*\*([^*]+)\*\*", _stash, text)
    escaped = _escape_latex(stashed)
    for i, keyword in enumerate(placeholders):
        escaped = escaped.replace(
            _escape_latex(_BN_TOKEN.format(i)),
            "\\bn{" + _escape_latex(keyword) + "}",
        )
    return f"{{\\small\\color{{BodyTx}}\n{escaped}\n}}"


def _emphasize_keywords(template: str, keywords: list[str]) -> str:
    """Bold JD keywords in Experience + Projects so PDFs visibly differ per job."""
    start = template.find("\\section{Experience}")
    skills = template.find("\\section{Technical Skills}")
    if start == -1 or skills == -1 or skills <= start:
        return template
    body = template[start:skills]
    ranked = sorted(
        {k.strip() for k in keywords if k and len(k.strip()) >= 3},
        key=len,
        reverse=True,
    )
    skip = {"and", "the", "for", "with", "app", "apps"}
    for kw in ranked:
        if kw.lower() in skip:
            continue
        pattern = re.compile(
            r"(?<!\\bn{)\b(" + re.escape(kw) + r")\b",
            re.IGNORECASE,
        )
        count = 0

        def _wrap(match: re.Match, _kw=kw) -> str:
            nonlocal count
            count += 1
            if count > 3:
                return match.group(0)
            return "\\bn{" + match.group(1) + "}"

        body = pattern.sub(_wrap, body)
    return template[:start] + body + template[skills:]


def _skills_to_latex(skills: dict) -> str:
    lines = ["\\section{Technical Skills}", ""]
    for category, items in skills.items():
        if not items:
            continue
        label = category if category.endswith(":") else f"{category}:"
        label = _escape_latex(label)
        escaped = ", ".join(_escape_latex(item) for item in items)
        lines.append(f"\\sk{{{label}}}{{{escaped}}}")
    return "\n".join(lines)


def _inject_skills(template: str, skills: dict) -> str:
    skills_block = _skills_to_latex(skills)
    start_marker = "\\section{Technical Skills}"
    start = template.find(start_marker)
    if start == -1:
        return template
    next_section = template.find("\\section{", start + len(start_marker))
    if next_section == -1:
        return template
    return template[:start] + skills_block + "\n\n" + template[next_section:]


def resume_base_name(role_category: str) -> str:
    """Deprecated alias — use folder_names.resume_file_basename."""
    from job_agent.resume.folder_names import format_role_label
    return f"Abhishek-Pandey_{format_role_label(role_category)}"


_HELVET_SCALE_RE = re.compile(r"(\\usepackage\[scaled=)([0-9.]+)(\]\{helvet\})")
_LINESPREAD_RE = re.compile(r"\\linespread\{[0-9.]+\}\s*%\s*auto-fit\n")

# (helvet scale, linespread) — applied in order until the resume fits one page
_FIT_STEPS: tuple[tuple[float, float], ...] = (
    (0.93, 1.00),
    (0.91, 0.99),
    (0.89, 0.98),
    (0.87, 0.97),
    (0.85, 0.96),
    (0.83, 0.95),
)


def _apply_fit_step(tex: str, scale: float, linespread: float) -> str:
    """Shrink body text so XeTeX output matches the intended one-page layout."""
    tex = _LINESPREAD_RE.sub("", tex)

    if _HELVET_SCALE_RE.search(tex):
        tex = _HELVET_SCALE_RE.sub(
            lambda m: f"{m.group(1)}{scale:g}{m.group(3)}", tex, count=1
        )

    if linespread < 1.0:
        anchor = "\\begin{document}"
        idx = tex.find(anchor)
        if idx != -1:
            tex = (
                tex[:idx]
                + f"\\linespread{{{linespread:g}}} % auto-fit\n"
                + tex[idx:]
            )
    return tex


def pdf_page_count(pdf_path: Path) -> int:
    """Page count, or 0 when it cannot be determined."""
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf_path)).pages)
    except Exception as e:  # missing dep or unreadable PDF — don't block generation
        logger.debug("pdf_page_count_failed", pdf=str(pdf_path), error=str(e)[:120])
        return 0


def _find_tectonic() -> str | None:
    for candidate in (
        shutil.which("tectonic"),
        "/opt/homebrew/bin/tectonic",
        "/usr/local/bin/tectonic",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return None


class LatexGenerator:
    def __init__(self):
        settings = get_settings()
        self.master_path = (
            Path(settings.master_resume_tex)
            if hasattr(settings, "master_resume_tex")
            else default_master_template()
        )
        if not self.master_path.exists():
            self.master_path = default_master_template()

    def generate(
        self,
        resume: TailoredResume | MasterResume,
        company: str,
        job_title: str,
        output_dir: Path,
        contact: MasterResume | None = None,
        match: JobMatch | None = None,
        role_category: str = "ANDROID",
        file_basename: str | None = None,
    ) -> tuple[Path, Path | None]:
        output_dir.mkdir(parents=True, exist_ok=True)
        settings = get_settings()

        template = self.master_path.read_text(encoding="utf-8")

        if isinstance(resume, TailoredResume):
            keywords = list(resume.keywords_used or [])
        else:
            keywords = []

        if match:
            keywords = list(dict.fromkeys(
                keywords + match.matching_skills + [match.job.title]
            ))

        # The Profile section and the header tagline are fixed, human-reviewed blocks
        # per resume profile: an Android resume always carries the Android master's
        # wording, a Backend resume the Backend master's. Per-job variation comes from
        # the skills list and keyword emphasis only, so the template is left as-is.
        tex_content = template

        if isinstance(resume, TailoredResume) and resume.technical_skills:
            tex_content = _inject_skills(tex_content, resume.technical_skills)

        tex_content = _emphasize_keywords(tex_content, keywords[:16])
        tex_content = _prepare_tex_for_tectonic(tex_content)

        fname_base = file_basename or resume_base_name(role_category)
        tex_path = output_dir / f"{fname_base}.tex"
        pdf_path = output_dir / f"{fname_base}.pdf"

        tex_path.write_text(tex_content, encoding="utf-8")
        logger.info("latex_generated", path=str(tex_path))

        compiled_pdf = None
        if settings.compile_pdf:
            compiled_pdf = self._compile_pdf(tex_path, pdf_path)
            if compiled_pdf:
                compiled_pdf = self._fit_to_max_pages(
                    tex_content, tex_path, pdf_path, compiled_pdf
                )

        return tex_path, compiled_pdf

    def generate_master_copy(self, output_dir: Path) -> tuple[Path, Path | None]:
        output_dir.mkdir(parents=True, exist_ok=True)
        settings = get_settings()
        tex_path = output_dir / "Abhishek_Pandey_Android_Engineer_Master.tex"
        pdf_path = output_dir / "Abhishek_Pandey_Android_Engineer_Master.pdf"
        shutil.copy(self.master_path, tex_path)
        compiled = self._compile_pdf(tex_path, pdf_path) if settings.compile_pdf else None
        return tex_path, compiled

    def _fit_to_max_pages(
        self,
        tex_content: str,
        tex_path: Path,
        pdf_path: Path,
        compiled_pdf: Path,
    ) -> Path:
        """Shrink and recompile until the PDF fits within resume_max_pages."""
        settings = get_settings()
        max_pages = int(getattr(settings, "resume_max_pages", 1) or 0)
        if max_pages <= 0:
            return compiled_pdf

        pages = pdf_page_count(compiled_pdf)
        if pages == 0 or pages <= max_pages:
            return compiled_pdf

        for scale, linespread in _FIT_STEPS:
            candidate = _apply_fit_step(tex_content, scale, linespread)
            tex_path.write_text(candidate, encoding="utf-8")
            result = self._compile_pdf(tex_path, pdf_path)
            if not result:
                break
            new_pages = pdf_page_count(result)
            if new_pages and new_pages <= max_pages:
                logger.info(
                    "resume_fitted_one_page",
                    path=str(pdf_path),
                    pages=new_pages,
                    scale=scale,
                    linespread=linespread,
                )
                return result
            pages = new_pages or pages

        # Restore the unshrunk source rather than leaving the tightest attempt
        tex_path.write_text(tex_content, encoding="utf-8")
        recompiled = self._compile_pdf(tex_path, pdf_path)
        logger.warning(
            "resume_page_fit_failed",
            path=str(pdf_path),
            pages=pages,
            max_pages=max_pages,
            hint="Trim resume content — shrinking alone could not fit the page",
        )
        return recompiled or compiled_pdf

    def _compile_pdf(self, tex_path: Path, pdf_path: Path) -> Path | None:
        settings = get_settings()
        compilers = [settings.latex_compiler, "tectonic", "pdflatex", "xelatex"]

        for compiler in compilers:
            if compiler == "tectonic":
                result = self._compile_tectonic(tex_path, pdf_path)
            else:
                result = self._compile_pdflatex(compiler, tex_path, pdf_path)
            if result:
                return result

        logger.warning(
            "pdf_compile_failed",
            tex=str(tex_path),
            hint="Install tectonic: brew install tectonic (or fix PATH for /opt/homebrew/bin)",
        )
        return None

    def _compile_pdflatex(self, compiler: str, tex_path: Path, pdf_path: Path) -> Path | None:
        exe = shutil.which(compiler) or compiler
        try:
            subprocess.run(
                [exe, "-interaction=nonstopmode", "-output-directory",
                 str(tex_path.parent), str(tex_path)],
                capture_output=True,
                timeout=90,
                check=True,
            )
            if pdf_path.exists():
                logger.info("pdf_compiled", path=str(pdf_path), compiler=compiler)
                return pdf_path
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    def _compile_tectonic(self, tex_path: Path, pdf_path: Path) -> Path | None:
        tectonic = _find_tectonic()
        if not tectonic:
            logger.warning(
                "tectonic_not_found",
                hint="Install: brew install tectonic",
            )
            return None

        # Tectonic 0.17+ uses --outdir / -o (not --output-dir)
        commands = [
            [tectonic, str(tex_path), "-o", str(tex_path.parent)],
            [tectonic, str(tex_path), "--outdir", str(tex_path.parent)],
            [tectonic, "-X", "compile", str(tex_path), "--outdir", str(tex_path.parent)],
        ]
        last_err = ""
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=180,
                    check=True,
                    text=True,
                )
                if pdf_path.exists():
                    logger.info("pdf_compiled", path=str(pdf_path), compiler="tectonic")
                    return pdf_path
                last_err = (result.stderr or result.stdout or "")[:500]
            except subprocess.CalledProcessError as e:
                last_err = ((e.stderr or "") + "\n" + (e.stdout or ""))[:800]
                logger.warning(
                    "tectonic_attempt_failed",
                    cmd=" ".join(cmd),
                    stderr=last_err[:400],
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                last_err = str(e)
        if last_err:
            logger.warning("tectonic_compile_error", tex=str(tex_path), detail=last_err[:500])
        return None
