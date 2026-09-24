/**
 * Structural invariants only.
 *
 * This file used to assert that ~90 literal strings appeared somewhere in
 * controller.ts. Those checks passed whether or not the UI worked, and failed on
 * any rename, so they blocked refactoring while proving nothing. Behaviour is now
 * covered by src/controller.test.tsx (the real controller driven through the real
 * React shell) and tests/e2e/release-controller.spec.ts.
 *
 * What is left here are the few rules a behaviour test cannot express: choices
 * about how the code is allowed to be structured, which no assertion about the
 * rendered DOM would notice being broken.
 */
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");

const app = read("src/App.tsx");
const controller = read("src/controller.ts");
const bpmnViewer = read("src/bpmnViewer.ts");
const api = read("src/api.ts");
const domain = read("src/domain.ts");
const styles = read("src/styles.css");
const viteConfig = read("vite.config.ts");

const checks = [
  [
    "React 必須擁有應用程式的生命週期",
    () => assert(app.includes("useReleaseController")),
  ],
  [
    "元件卡片只能有一份樣板，由專案的元件清單展開",
    () => {
      // v3.0 起元件數量與名稱是專案資料，不是程式碼。卡片樣板寫死成兩份的話，
      // 第三個元件就會沒有卡片，而且不會有任何測試發現。
      assert(
        app.includes('id="component-grid"'),
        "App.tsx 必須留下空的 #component-grid 容器",
      );
      assert(
        !app.includes("ComponentCard"),
        "元件卡片不可以在 React 殼層裡逐一寫死",
      );
      const templates = controller.match(/class="component-card"/g) ?? [];
      assert(
        templates.length === 1,
        `元件卡片樣板出現 ${templates.length} 次，只允許 renderComponentCards 裡的那一份`,
      );
      assert(controller.includes("function renderComponentCards("));
      assert(
        /activeComponents\(\)[\s\S]{0,200}?\.map\(/.test(
          controller.slice(
            controller.indexOf("function renderComponentCards("),
          ),
        ),
        "卡片必須由 activeComponents() 展開",
      );
    },
  ],
  [
    "每個元件的 build 選取與分支必須互相獨立",
    () => {
      // 以元件 key 為索引的表，而不是寫死的兩個欄位：專案換了就整批換掉。
      for (const field of [
        "builds",
        "branches",
        "selectedBranch",
        "selected",
        "included",
      ]) {
        assert(
          new RegExp(`${field}:\\s*Record<ComponentName,`).test(controller),
          `state.${field} 必須是以 ComponentName 為索引的表`,
        );
      }
      const initialiser = controller.slice(
        controller.indexOf("function initialLegacyState(): LegacyState {"),
        controller.indexOf("const state: LegacyState = initialLegacyState();"),
      );
      assert(initialiser, "找不到初始狀態的工廠函式 initialLegacyState()");
      assert(
        !/\b(frontend|backend)\s*:/.test(initialiser),
        "初始狀態不可以寫死 frontend / backend 這兩個 key",
      );
    },
  ],
  [
    "選取歷史記錄不可以重新載入整份文件",
    () => {
      assert(!controller.includes("window.location.assign("));
      assert(!controller.includes("window.location.reload("));
      assert(!/window\.location\.href\s*=/.test(controller));
    },
  ],
  [
    "BPMN 必須用可平移縮放的 viewer，而且不可以綁進 controller chunk",
    () => {
      assert(
        bpmnViewer.includes("bpmn-js/lib/NavigatedViewer"),
        "src/bpmnViewer.ts 必須匯出 NavigatedViewer；基礎 Viewer 不能平移縮放",
      );
      // bpmn-js 是整包前端最大的相依。靜態 import 會把它綁進 controller chunk，
      // 讓登入後的介面必須等它下載完才會有反應。
      assert(
        !/from\s+['"]\.\/bpmnViewer['"]/.test(controller),
        "controller.ts 不可以靜態 import bpmnViewer",
      );
      assert(
        controller.includes("import('./bpmnViewer')"),
        "controller.ts 必須以動態 import 載入 bpmnViewer",
      );
    },
  ],
  [
    "列表與 detail 的渲染必須走冪等的 patch，不可直接寫 innerHTML",
    () => {
      // 直接指派會在每次輪詢重建一份一模一樣的 DOM：面板閃一下、捲動位置歸零、
      // BPMN 被重新 import。整支檔案只允許 patchDetail 裡那一次指派。
      assert(controller.includes("function patchDetail("));
      assert(controller.includes("function patchRows("));
      const assignments =
        controller.match(/elements\.(history|detail)\.innerHTML\s*=/g) ?? [];
      assert(
        assignments.length === 1,
        `elements.history/detail.innerHTML 被直接指派 ${assignments.length} 次，只允許 patchDetail 內的那一次`,
      );
      const patchDetail = controller.slice(
        controller.indexOf("function patchDetail("),
        controller.indexOf("function patchDetail(") + 400,
      );
      assert(
        patchDetail.includes("elements.detail.innerHTML = html;"),
        "唯一的 innerHTML 指派必須留在 patchDetail 裡",
      );
    },
  ],
  [
    "會被複用的列必須用委派綁定事件，不可在 render 時逐一綁定",
    () => {
      assert(!/list\.querySelectorAll\([^)]*\)\.forEach/.test(controller));
      // 換專案會整個重畫 #component-grid，所以委派必須綁在不會被換掉的容器上。
      assert(
        controller.includes("'#component-grid').addEventListener('click'"),
        "build 列的點擊必須委派給 #component-grid",
      );
      assert(
        controller.includes("'#component-grid').addEventListener('change'"),
        "Include 勾選與分支切換必須委派給 #component-grid",
      );
    },
  ],
  [
    "API 客戶端必須提供有型別的泛型請求函式",
    () => assert(api.includes("export async function api<T>")),
  ],
  [
    "元件名稱是使用者資料，其餘領域型別仍必須是封閉的聯集",
    () => {
      // v3.0 之前這裡要求 ComponentName 是 'frontend' | 'backend'。元件現在由使用者
      // 定義，所以它必須是開放的；但只有它一個，其他狀態列舉不可以跟著鬆掉成 string。
      assert(
        /export type ComponentName = string;/.test(domain),
        "ComponentName 必須是具名別名，讓元件身分在型別上仍看得出來",
      );
      assert(
        /\bkey:\s*ComponentName;/.test(domain),
        "ProjectComponent.key 必須用 ComponentName，不可退回裸 string",
      );
      assert(
        /\bcomponent:\s*ComponentName;/.test(domain),
        "Deployment.component 必須用 ComponentName，不可退回裸 string",
      );
      for (const closed of [
        "ReleaseMode",
        "PublishStatus",
        "ConnectionKind",
        "HistoryView",
      ]) {
        const declaration = new RegExp(
          `export type ${closed} =[\\s\\S]*?;`,
        ).exec(domain);
        assert(declaration, `缺少 ${closed} 型別宣告`);
        assert(
          !/\bstring\b/.test(declaration[0]),
          `${closed} 必須維持字面值聯集，不可退回 string`,
        );
      }
    },
  ],
  [
    "連線的 token 只能單向送出，不可讀回或畫進畫面",
    () => {
      // API 回的是 token_hint，不是 token。這條擋的是「編輯時順手把舊值填回輸入框」
      // 那種改法——一旦填回去，token 就會出現在 DOM 裡。
      assert(
        /name="token"[\s\S]{0,200}?type="password"/.test(app),
        "connection 表單的 token 必須是 password 欄位",
      );
      assert(
        !/token["']?\s*\]?\s*(?:\.value\s*=|=\s*connection\.token\b)/.test(
          controller,
        ),
        "不可以把 token 寫回輸入框",
      );
      assert(
        !/\bconnection\.token\b(?!_hint)/.test(controller),
        "controller 不該讀 connection.token —— API 只回 token_hint",
      );
    },
  ],
  [
    "controller.ts 必須被納入覆蓋率統計",
    () => {
      const coverage = viteConfig.slice(viteConfig.indexOf("coverage:"));
      assert(
        !coverage.includes('"src/controller.ts"'),
        "controller.ts 被排除在覆蓋率之外，等於 1,500 行沒有下限保護",
      );
    },
  ],
  [
    "BPMN 進度標記的樣式必須存在",
    () => {
      for (const marker of [
        "workflow-completed",
        "workflow-current",
        "workflow-failed",
        "workflow-cancelled",
      ]) {
        assert(styles.includes(marker), `缺少 BPMN 進度標記樣式：${marker}`);
      }
    },
  ],
];

let failed = 0;
for (const [name, check] of checks) {
  try {
    check();
  } catch (error) {
    failed += 1;
    console.error(`✗ ${name}\n  ${error.message}`);
  }
}

if (failed > 0) {
  console.error(`\n${failed}/${checks.length} 項結構不變式未通過`);
  process.exit(1);
}
console.log(`UI structural invariants passed (${checks.length} checks)`);
