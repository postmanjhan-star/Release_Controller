/**
 * Everything bpmn-js, behind one lazily imported module.
 *
 * The viewer and its stylesheets are the largest thing this app ships, and they
 * are needed only once a record with a diagram is opened. Keeping them here
 * means the controller chunk -- which every signed-in operator downloads before
 * the interface responds -- does not carry them.
 */
import NavigatedViewer from "bpmn-js/lib/NavigatedViewer";
import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-font/css/bpmn.css";

export default NavigatedViewer;
