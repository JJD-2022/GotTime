const stackItems = [
  {
    name: 'React',
    description: 'A modern component-driven layer for interactive UI experiences and animated state updates.'
  },
  {
    name: 'Flask',
    description: 'The backend engine powering auth, routing, sessions, notifications, and database workflows.'
  },
  {
    name: 'MySQL',
    description: 'Reliable storage for users, skills, sessions, credits, reviews, and moderation data.'
  }
];

function TechStackPanel() {
  return React.createElement(
    'div',
    { className: 'row mt-4 g-4' },
    stackItems.map((item) => React.createElement(
      'div',
      { className: 'col-md-4', key: item.name },
      React.createElement(
        'div',
        { className: 'card h-100 p-4' },
        React.createElement('h4', null, item.name),
        React.createElement('p', { className: 'text-muted mb-0' }, item.description)
      )
    ))
  );
}

const reactRoot = document.getElementById('reactStack');
if (reactRoot) {
  ReactDOM.createRoot(reactRoot).render(React.createElement(TechStackPanel));
}
