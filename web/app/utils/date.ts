export const formatDate = (timestamp: string, addDays = 0) => {
  const date = new Date(timestamp)

  if (addDays !== 0) {
    date.setDate(date.getDate() + addDays)
  }

  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric'
  })
}
