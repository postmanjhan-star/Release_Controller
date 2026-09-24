import { createRoot } from "react-dom/client";
import { Provider } from "react-redux";
import { App } from "./App";
import { store } from "./store";
// The bpmn-js stylesheets are imported by controller.ts, which is loaded lazily
// after sign-in. Importing them here as well pulled ~33kB of CSS for a diagram
// nobody has asked for into the entry bundle, on the login screen included.
import "./styles.css";

const rootElement = document.querySelector<HTMLDivElement>("#root");
if (!rootElement) throw new Error("React root element was not found");

createRoot(rootElement).render(
  <Provider store={store}>
    <App />
  </Provider>,
);
