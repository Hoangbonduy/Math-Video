const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/idea/web.js'), 'utf8');
const root = {children:[]};
const context = vm.createContext({
  root,
  el(tag, text, parent, className) {
    const node = {tag, text, className, children:[], style:{}};
    parent.children.push(node);
    return node;
  }
});
vm.runInContext(source, context);
context.script = {global_visuals:{objects:'paper <b>literal</b>',layout:'Chính diện',math:'mm',continuity:'Giữ ID'}};
vm.runInContext('renderGlobalVisuals(root, script)', context);
assert.equal(root.children.length, 1);
assert.equal(root.children[0].children[0].text, 'Khai báo toàn cục');
assert.equal(root.children[0].children[2].text, 'paper <b>literal</b>');
context.visual = '1. Kế thừa & thay đổi\nKế thừa: s1\n2. Timeline hình ảnh\n- 0s → paper → giữ → Không có → hiện\nKết cảnh: paper\n3. Tiêu chí đạt\n- Rõ\n- Đúng thứ tự';
vm.runInContext('renderSceneVisuals(root, visual)', context);
const headings = root.children[1].children.filter(n => n.tag === 'h4').map(n => n.text);
assert.deepEqual(headings, ['Hình ảnh','1. Kế thừa & thay đổi','2. Timeline hình ảnh','3. Tiêu chí đạt']);
context.visual = 'Mô tả cũ';
vm.runInContext('renderSceneVisuals(root, visual); renderGlobalVisuals(root, {})', context);
assert.equal(root.children.length, 3);
assert.equal(root.children[2].children[1].text, 'Mô tả cũ');
console.log('PASS: global declarations, three visual sections, literal text, legacy display');
