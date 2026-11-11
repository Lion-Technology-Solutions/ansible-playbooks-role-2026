'use strict';
const lessonSearch = document.querySelector('[data-lesson-search]');
if (lessonSearch) {
  lessonSearch.addEventListener('input', () => {
    const query = lessonSearch.value.trim().toLocaleLowerCase();
    let count = 0;
    document.querySelectorAll('.devops-module').forEach(module => {
      let visible = 0;
      module.querySelectorAll('[data-lesson-row]').forEach(row => {
        row.hidden = !row.querySelector('.devops-lesson-name').textContent.toLocaleLowerCase().includes(query);
        if (!row.hidden) visible++;
      });
      module.hidden = visible === 0;
      count += visible;
    });
    document.querySelector('[data-lesson-count]').textContent = count + (count === 1 ? ' lesson' : ' lessons');
    document.querySelector('[data-lesson-empty]').hidden = count !== 0;
  });
}
