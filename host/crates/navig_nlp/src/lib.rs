//! navig_nlp — PyO3 extension for hot-path NLP in NAVIG.
//!
//! Exposes three functions to Python:
//!   • `tokenize_and_score(text, stop_words)` → Dict[str, float]  (augmented TF)
//!   • `batch_tokenize(texts, stop_words)`    → Vec<Dict[str, float>>
//!   • `is_low_signal(text)`                  → bool
//!
//! All regexes are compiled once (Lazy<Regex>) and reused across calls.
//! `batch_tokenize` uses Rayon for parallel processing of large batches.

use ahash::AHashMap;
use once_cell::sync::Lazy;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use rayon::prelude::*;
use regex::Regex;

// ── Compiled patterns (one-time init) ─────────────────────────────────────

static STRIP_CODE_BLOCK: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?s)```[\s\S]*?```").unwrap());

static STRIP_INLINE_CODE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"`[^`]+`").unwrap());

static STRIP_URL: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"https?://\S+").unwrap());

static TOKEN_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"[a-z0-9]{3,}").unwrap());

// Low-signal patterns — exact parity with fact_extractor.py
static LOW_SIGNAL: Lazy<Vec<Regex>> = Lazy::new(|| {
    vec![
        Regex::new(r"(?i)^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no|bye|great|good|nice|cool)\s*[.!?]*$").unwrap(),
        Regex::new(r"(?i)^(what|how|why|when|where|can you|could you|please|help)\s").unwrap(),
        Regex::new(r"(?i)^(show me|list|display|print|run|execute|debug|fix|build|deploy)\s").unwrap(),
    ]
});

// ── Core tokenizer ────────────────────────────────────────────────────────

/// Strip markdown code blocks, inline code, and URLs, then extract 3+ char
/// alphanumeric tokens, filtering out stop words. Exact parity with the
/// Python `_tokenize()` in `inbox_router.py`.
fn tokenize(text: &str, stop_words: &ahash::AHashSet<String>) -> Vec<String> {
    let cleaned = STRIP_CODE_BLOCK.replace_all(text, " ");
    let cleaned = STRIP_INLINE_CODE.replace_all(&cleaned, " ");
    let cleaned = STRIP_URL.replace_all(&cleaned, " ");
    let lower = cleaned.to_lowercase();

    TOKEN_RE
        .find_iter(&lower)
        .map(|m| m.as_str().to_owned())
        .filter(|t| !stop_words.contains(t))
        .collect()
}

/// Augmented term frequency: `0.5 + 0.5 * (count / max_count)`.
/// Exact parity with the Python `_term_frequency()`.
fn term_frequency(tokens: &[String]) -> AHashMap<String, f64> {
    let mut counts: AHashMap<String, u32> = AHashMap::new();
    for t in tokens {
        *counts.entry(t.clone()).or_insert(0) += 1;
    }
    let max_f = counts.values().copied().max().unwrap_or(1) as f64;
    counts
        .into_iter()
        .map(|(t, c)| (t, 0.5 + 0.5 * (c as f64 / max_f)))
        .collect()
}

// ── Python-exposed functions ──────────────────────────────────────────────

/// Tokenize text and return augmented term-frequency scores as a Python dict.
///
/// This fuses `_tokenize()` + `_term_frequency()` into a single native call,
/// eliminating the Python list allocation + two function-call overheads.
#[pyfunction]
fn tokenize_and_score<'py>(
    py: Python<'py>,
    text: &str,
    stop_words: &Bound<'py, PyList>,
) -> PyResult<Bound<'py, PyDict>> {
    let sw: ahash::AHashSet<String> = stop_words
        .iter()
        .filter_map(|item| item.extract::<String>().ok())
        .collect();

    let tokens = tokenize(text, &sw);
    let tf = term_frequency(&tokens);

    let dict = PyDict::new_bound(py);
    for (term, score) in &tf {
        dict.set_item(term, *score)?;
    }
    Ok(dict)
}

/// Batch-tokenize multiple texts in parallel using Rayon.
/// Returns a list of dicts, one per input text.
#[pyfunction]
fn batch_tokenize<'py>(
    py: Python<'py>,
    texts: &Bound<'py, PyList>,
    stop_words: &Bound<'py, PyList>,
) -> PyResult<Bound<'py, PyList>> {
    let sw: ahash::AHashSet<String> = stop_words
        .iter()
        .filter_map(|item| item.extract::<String>().ok())
        .collect();

    let rust_texts: Vec<String> = texts
        .iter()
        .filter_map(|item| item.extract::<String>().ok())
        .collect();

    // Release the GIL for the CPU-bound parallel work
    let results: Vec<AHashMap<String, f64>> = py.allow_threads(|| {
        rust_texts
            .par_iter()
            .map(|t| {
                let tokens = tokenize(t, &sw);
                term_frequency(&tokens)
            })
            .collect()
    });

    let out = PyList::empty_bound(py);
    for tf in &results {
        let dict = PyDict::new_bound(py);
        for (term, score) in tf {
            dict.set_item(term, *score)?;
        }
        out.append(dict)?;
    }
    Ok(out)
}

/// Fast low-signal detection. Returns True if the text matches any of the
/// low-signal patterns (greetings, bare commands, questions without substance).
/// Exact parity with `_is_low_signal()` in `fact_extractor.py`.
#[pyfunction]
fn is_low_signal(text: &str) -> bool {
    let trimmed = text.trim();
    for pat in LOW_SIGNAL.iter() {
        if pat.is_match(trimmed) {
            return true;
        }
    }
    false
}

// ── Module registration ───────────────────────────────────────────────────

/// Python module: `import navig_nlp`
#[pymodule]
fn navig_nlp(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(tokenize_and_score, m)?)?;
    m.add_function(wrap_pyfunction!(batch_tokenize, m)?)?;
    m.add_function(wrap_pyfunction!(is_low_signal, m)?)?;
    Ok(())
}

// ── Tests ─────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn sw() -> ahash::AHashSet<String> {
        ["the", "and", "for", "are", "but", "not", "you", "all"]
            .iter()
            .map(|s| s.to_string())
            .collect()
    }

    #[test]
    fn tokenize_strips_code_blocks() {
        let text = "hello ```rust\nfn main() {}\n``` world";
        let tokens = tokenize(text, &sw());
        assert!(tokens.contains(&"hello".to_string()));
        assert!(tokens.contains(&"world".to_string()));
        assert!(!tokens.contains(&"main".to_string()));
        assert!(!tokens.contains(&"rust".to_string()));
    }

    #[test]
    fn tokenize_strips_inline_code() {
        let text = "use `HashMap` for storing data";
        let tokens = tokenize(text, &sw());
        assert!(!tokens.contains(&"hashmap".to_string()));
        assert!(tokens.contains(&"use".to_string()));
        assert!(tokens.contains(&"storing".to_string()));
        assert!(tokens.contains(&"data".to_string()));
    }

    #[test]
    fn tokenize_strips_urls() {
        let text = "visit https://example.com/path?q=1 for docs";
        let tokens = tokenize(text, &sw());
        assert!(!tokens.contains(&"https".to_string()));
        assert!(!tokens.contains(&"example".to_string()));
        assert!(tokens.contains(&"visit".to_string()));
        assert!(tokens.contains(&"docs".to_string()));
    }

    #[test]
    fn tokenize_filters_stop_words() {
        let text = "the quick and beautiful fox";
        let tokens = tokenize(text, &sw());
        assert!(!tokens.contains(&"the".to_string()));
        assert!(!tokens.contains(&"and".to_string()));
        assert!(tokens.contains(&"quick".to_string()));
        assert!(tokens.contains(&"beautiful".to_string()));
        assert!(tokens.contains(&"fox".to_string()));
    }

    #[test]
    fn tokenize_min_length_3() {
        let text = "I am a go developer in US";
        let tokens = tokenize(text, &sw());
        // "I", "am", "a", "go", "in", "US" are all < 3 chars
        assert!(!tokens.contains(&"go".to_string()));
        assert!(tokens.contains(&"developer".to_string()));
    }

    #[test]
    fn term_frequency_augmented() {
        let tokens: Vec<String> = vec!["rust".into(), "rust".into(), "python".into()];
        let tf = term_frequency(&tokens);
        // "rust" appears 2x (max), so TF = 0.5 + 0.5 * (2/2) = 1.0
        assert!((tf["rust"] - 1.0).abs() < f64::EPSILON);
        // "python" appears 1x, so TF = 0.5 + 0.5 * (1/2) = 0.75
        assert!((tf["python"] - 0.75).abs() < f64::EPSILON);
    }

    #[test]
    fn term_frequency_single_token() {
        let tokens: Vec<String> = vec!["only".into()];
        let tf = term_frequency(&tokens);
        assert!((tf["only"] - 1.0).abs() < f64::EPSILON);
    }

    #[test]
    fn term_frequency_empty() {
        let tokens: Vec<String> = vec![];
        let tf = term_frequency(&tokens);
        assert!(tf.is_empty());
    }

    #[test]
    fn low_signal_greetings() {
        assert!(is_low_signal("hi"));
        assert!(is_low_signal("Hello!"));
        assert!(is_low_signal("thanks!"));
        assert!(is_low_signal("ok"));
        assert!(is_low_signal("bye"));
        assert!(is_low_signal("  great  "));
    }

    #[test]
    fn low_signal_commands() {
        assert!(is_low_signal("show me the logs"));
        assert!(is_low_signal("run the tests"));
        assert!(is_low_signal("debug this function"));
        assert!(is_low_signal("deploy to production"));
    }

    #[test]
    fn low_signal_questions() {
        assert!(is_low_signal("what is the status?"));
        assert!(is_low_signal("how do I install this?"));
        assert!(is_low_signal("can you help me?"));
    }

    #[test]
    fn high_signal_not_low() {
        assert!(!is_low_signal(
            "I prefer using PostgreSQL for all new projects going forward"
        ));
        assert!(!is_low_signal(
            "Our team decided to migrate from MySQL to Postgres yesterday"
        ));
        assert!(!is_low_signal(
            "I work at Acme Corp as a senior backend engineer in Berlin timezone CET"
        ));
    }

    #[test]
    fn combined_workflow() {
        let sw = sw();
        let text = "```python\nimport os\n```\nWe decided to use PostgreSQL https://pg.dev for caching.\nThe team prefers Redis for sessions.";
        let tokens = tokenize(text, &sw);

        // Code block content stripped
        assert!(!tokens.contains(&"import".to_string()));
        assert!(!tokens.contains(&"python".to_string()));
        // URL stripped
        assert!(!tokens.iter().any(|t| t.contains("pg.dev")));
        // Real content preserved
        assert!(tokens.contains(&"decided".to_string()));
        assert!(tokens.contains(&"postgresql".to_string()));
        assert!(tokens.contains(&"redis".to_string()));

        let tf = term_frequency(&tokens);
        assert!(tf.contains_key("postgresql"));
        // not low signal
        assert!(!is_low_signal(text));
    }
}
