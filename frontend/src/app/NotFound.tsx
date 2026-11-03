import { Link } from 'react-router'

export function NotFound() {
  return (
    <section>
      <h1 className="text-xl font-semibold">Page not found</h1>
      <Link to="/" className="text-blue-700 underline">
        Back to home
      </Link>
    </section>
  )
}
