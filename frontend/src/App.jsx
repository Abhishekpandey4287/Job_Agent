import { useState, useEffect, useCallback } from 'react'
import { BrowserRouter, Routes, Route, NavLink, useNavigate, useParams } from 'react-router-dom'

const API = '/api'

function useFetch(url) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refetch = useCallback(() => {
    setLoading(true)
    fetch(url)
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json() })
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [url])

  useEffect(() => { refetch() }, [refetch])
  return { data, loading, error, refetch }
}

function scoreClass(score) {
  if (score >= 80) return 'score-high'
  if (score >= 60) return 'score-mid'
  return 'score-low'
}

function badgeClass(cat) {
  if (!cat) return ''
  if (cat.includes('Excellent')) return 'badge-excellent'
  if (cat.includes('Strong')) return 'badge-strong'
  if (cat.includes('Good')) return 'badge-good'
  return 'badge-moderate'
}

function Overview() {
  const { data: stats, loading } = useFetch(`${API}/dashboard/stats`)
  const navigate = useNavigate()
  const [running, setRunning] = useState(false)

  async function runSearch() {
    setRunning(true)
    try {
      await fetch(`${API}/pipeline/run`, { method: 'POST' })
      window.location.reload()
    } catch (e) {
      alert('Pipeline failed: ' + e.message)
    } finally {
      setRunning(false)
    }
  }

  if (loading) return <div className="loading">Loading...</div>

  return (
    <div>
      <h2 className="page-title">Overview</h2>
      <div className="actions">
        <button className="btn btn-primary" onClick={runSearch} disabled={running}>
          {running ? 'Searching...' : 'Run Job Search Now'}
        </button>
        <button className="btn btn-secondary" onClick={() => navigate('/jobs')}>
          View All Jobs
        </button>
      </div>
      <div className="stats-grid">
        <div className="stat-card"><div className="value">{stats?.jobs_found_today ?? 0}</div><div className="label">Jobs Found Today</div></div>
        <div className="stat-card"><div className="value">{stats?.strong_matches ?? 0}</div><div className="label">Strong Matches</div></div>
        <div className="stat-card"><div className="value">{stats?.applications_ready ?? 0}</div><div className="label">Ready to Apply</div></div>
        <div className="stat-card"><div className="value">{stats?.applications_submitted ?? 0}</div><div className="label">Applied</div></div>
        <div className="stat-card"><div className="value">{stats?.interviews ?? 0}</div><div className="label">Interviews</div></div>
        <div className="stat-card"><div className="value">{stats?.rejections ?? 0}</div><div className="label">Rejections</div></div>
        <div className="stat-card"><div className="value">{stats?.offers ?? 0}</div><div className="label">Offers</div></div>
      </div>
    </div>
  )
}

function JobsList() {
  const [filters, setFilters] = useState({ status: '', min_score: '', company: '' })
  const query = new URLSearchParams()
  if (filters.status) query.set('status', filters.status)
  if (filters.min_score) query.set('min_score', filters.min_score)
  if (filters.company) query.set('company', filters.company)
  const url = `${API}/jobs?${query}&limit=100`
  const { data: jobs, loading } = useFetch(url)
  const navigate = useNavigate()

  return (
    <div>
      <h2 className="page-title">Today's Jobs</h2>
      <div className="filter-bar">
        <input placeholder="Company..." value={filters.company}
          onChange={e => setFilters(f => ({ ...f, company: e.target.value }))} />
        <select value={filters.status} onChange={e => setFilters(f => ({ ...f, status: e.target.value }))}>
          <option value="">All Statuses</option>
          <option value="DISCOVERED">Discovered</option>
          <option value="MATCHED">Matched</option>
          <option value="RESUME_GENERATED">Resume Generated</option>
          <option value="READY_TO_APPLY">Ready to Apply</option>
          <option value="APPLIED">Applied</option>
          <option value="INTERVIEW">Interview</option>
          <option value="REJECTED">Rejected</option>
        </select>
        <select value={filters.min_score} onChange={e => setFilters(f => ({ ...f, min_score: e.target.value }))}>
          <option value="">Any Score</option>
          <option value="90">90+</option>
          <option value="80">80+</option>
          <option value="70">70+</option>
          <option value="60">60+</option>
        </select>
      </div>
      {loading ? <div className="loading">Loading jobs...</div> : (
        <div className="jobs-table">
          <table>
            <thead>
              <tr>
                <th>Company</th><th>Title</th><th>Location</th>
                <th>Score</th><th>Category</th><th>Status</th><th>Source</th>
              </tr>
            </thead>
            <tbody>
              {(jobs || []).map(j => (
                <tr key={j.job_id} style={{ cursor: 'pointer' }} onClick={() => navigate(`/jobs/${j.job_id}`)}>
                  <td>{j.company}</td>
                  <td>{j.title}</td>
                  <td>{j.location}</td>
                  <td><span className={`score ${scoreClass(j.match_score)}`}>{j.match_score?.toFixed(0) ?? '—'}%</span></td>
                  <td><span className={`badge ${badgeClass(j.match_category)}`}>{j.match_category ?? '—'}</span></td>
                  <td>{j.application_status}</td>
                  <td>{j.source}</td>
                </tr>
              ))}
              {(!jobs || jobs.length === 0) && (
                <tr><td colSpan={7} style={{ textAlign: 'center', padding: 32 }}>No jobs found. Run a search to discover opportunities.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function JobDetail() {
  const { jobId } = useParams()
  const { data: job, loading, refetch } = useFetch(`${API}/jobs/${jobId}`)

  async function updateStatus(status) {
    await fetch(`${API}/jobs/${jobId}/status`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    })
    refetch()
  }

  async function apply() {
    await fetch(`${API}/jobs/${jobId}/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approve: true }),
    })
    refetch()
  }

  if (loading) return <div className="loading">Loading...</div>
  if (!job) return <div className="loading">Job not found</div>

  return (
    <div>
      <h2 className="page-title">{job.title} — {job.company}</h2>
      <div className="stats-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
        <div className="stat-card"><div className="value">{job.match_score?.toFixed(0)}%</div><div className="label">Match Score</div></div>
        <div className="stat-card"><div className="value">{job.location}</div><div className="label">Location</div></div>
        <div className="stat-card"><div className="value">{job.application_status}</div><div className="label">Status</div></div>
        <div className="stat-card"><div className="value">{job.salary || 'N/A'}</div><div className="label">Salary</div></div>
      </div>

      <div className="actions">
        {job.application_url && (
          <a className="btn btn-primary" href={job.application_url} target="_blank" rel="noopener">Open Application</a>
        )}
        <button className="btn btn-success" onClick={apply}>Approve & Apply</button>
        <button className="btn btn-secondary" onClick={() => updateStatus('APPLIED')}>Mark Applied</button>
        <button className="btn btn-secondary" onClick={() => updateStatus('INTERVIEW')}>Mark Interview</button>
        <button className="btn btn-secondary" onClick={() => updateStatus('REJECTED')}>Mark Rejected</button>
        {job.resume_tex_path && (
          <a className="btn btn-secondary" href={`${API}/resume/download/${jobId}?format=tex`}>Download .tex</a>
        )}
        {job.resume_path && (
          <a className="btn btn-secondary" href={`${API}/resume/download/${jobId}?format=pdf`}>Download PDF</a>
        )}
      </div>

      <div className="detail-panel">
        <h3>Match Analysis</h3>
        <p>{job.match_reason}</p>
        <p><strong>Matching Skills:</strong> {job.matching_skills}</p>
        <p><strong>Missing Requirements:</strong> {job.missing_requirements}</p>
      </div>

      <div className="detail-panel">
        <h3>Job Description</h3>
        <pre>{job.job_description || 'No description available'}</pre>
      </div>
    </div>
  )
}

function MasterResume() {
  const { data: resume, loading } = useFetch(`${API}/resume/master`)
  if (loading) return <div className="loading">Loading...</div>
  return (
    <div>
      <h2 className="page-title">Master Resume</h2>
      <div className="detail-panel">
        <h3>{resume?.contact?.name}</h3>
        <p>{resume?.contact?.email} | {resume?.contact?.phone} | {resume?.contact?.location}</p>
        <h3 style={{ marginTop: 16 }}>Profile</h3>
        <p>{resume?.profile_summary}</p>
        <h3 style={{ marginTop: 16 }}>Experience</h3>
        {(resume?.experience || []).map((e, i) => (
          <div key={i} style={{ marginBottom: 12 }}>
            <strong>{e.title}</strong> at {e.company} ({e.start_date} – {e.end_date})
            <ul>{e.bullets.map((b, j) => <li key={j}>{b}</li>)}</ul>
          </div>
        ))}
      </div>
    </div>
  )
}

function Tracker() {
  return <JobsList />
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="app">
        <aside className="sidebar">
          <h1>Job Search Agent</h1>
          <nav>
            <NavLink to="/" end>Overview</NavLink>
            <NavLink to="/jobs">Today's Jobs</NavLink>
            <NavLink to="/tracker">Application Tracker</NavLink>
            <NavLink to="/resume">Master Resume</NavLink>
          </nav>
        </aside>
        <main className="main">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/jobs" element={<JobsList />} />
            <Route path="/jobs/:jobId" element={<JobDetail />} />
            <Route path="/tracker" element={<Tracker />} />
            <Route path="/resume" element={<MasterResume />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
