/*
object.js is the javascript used to animate the page object.html

All functions present here are solely used on the object.html page

NOTE: the object.html and object.js are used to render the following EasyUCS Objects: Backups, Configs, Inventories, and Reports
*/

// Global variables
var device_uuid = "";
var current_device = null;
var is_catalog = false;
var object_type = "";
var object_uuid = "";
var object = null;
var object_json = null;

// Gets executed when DOM has loaded
function afterDOMloaded() {
  // Adding class to menu element in base.html to reflect that it is open
  document.getElementById("navLinkDevices").className += " active" 

    // Forces page reload when using browser back button
    if(typeof window.performance != "undefined" && window.performance.navigation.type == 2){
        window.location.reload(true);
    }
    url = window.location.href;
    device_uuid = url.split("/")[4];
    object_type = url.split("/")[5]; // backup, config, inventory, or report
    object_uuid = url.substring(url.lastIndexOf('/') + 1);

    refreshData();
};

/**
 * Deletes an object
 * @param  {Event} event - The event generated by the click
 */
function deleteObject(event){
  event.preventDefault();
  removeEventPropagation(event);

  // Only executes if the user confirms the warning
  Swal.fire({  
    title: "Do you really want to delete the following " + object_type + "?",
    text: setObjectName(object, object_type),
    showDenyButton: true,
    confirmButtonText: `Yes`,
    denyButtonText: `No`,
  }).then((result) => {
      if (result.isConfirmed) {
        // Deletes the object and redirects the user to the device view
        deleteFromDb(redirectToDevice, object_type, device_uuid, object_uuid);
      }
  });
}

/**
 * Displays an object in the UI
 * @param  {JSON} data - The data returned by getting the object from the API
 */
function displayObject(data){
  if(!data){
    console.error('No data to display!')
    return
  }

  data = JSON.parse(data);

  if(!data[object_type]){
    console.error("Impossible to display object " + object_type + ": no data provided");
    return
  }

  // We refresh the object displayed only if there were changes in the data
  if(_.isEqual(object, data[object_type])){
    return
  }

  object = data[object_type];

  var display_name = setObjectName(object, object_type);

  // Changes the object display based on the device type
  if(current_device.device_type == "intersight"){
      avatar_src = "/static/img/intersight_logo.png";
      color = "bg-info";
  } else if (current_device.device_type == "ucsm"){
      avatar_src = "/static/img/ucsm_logo.png";
      color = "bg-primary";
  } else if (current_device.device_type == "cimc"){
      avatar_src = "/static/img/cimc_logo.png";
      color = "bg-warning";
  } else if (current_device.device_type == "ucsc"){
      avatar_src = "/static/img/ucsc_logo.png";
      color = "bg-dark";
  }

  if(current_device.is_system){
    if(current_device.system_usage == "catalog"){
      is_catalog = true;
    }
  }

  

  // Populates the object's display container with the object's UI elements
  if(!is_catalog){

    // Updates the breadcrumb device url with the url of the object's parent device
    document.getElementById('parent_device_breadcrumb_link').textContent = "Device";
    document.getElementById('parent_device_breadcrumb_link').href = "/devices/" + object.device_uuid;

    document.getElementById('objectDisplayContainer').innerHTML =
    `
    <div>
      <div class="card card-widget widget-user-2 mb-0">
        <div class="widget-user-header p-0">
          <div class = "row m-0">
            <div class = "col-md-6 ${color} p-3 d-flex flex-column justify-content-between rounded-left shadow">
              <div>
                <div class="widget-user-image">
                  <img class="img-circle elevation-2" src=${avatar_src} alt="Device Type">
                </div>
                <h3 class="widget-user-username text-truncate" data-toggle="tooltip" data-placement="top" title="${display_name}">
                ${display_name}
                </h3>
                <h5 class="widget-user-desc text-truncate" data-toggle="tooltip" data-placement="top" title="${current_device.device_name}">
                  on ${current_device.device_name}
                </h5>
              </div>
              
              <div class = "d-flex flex-row p-2">
                <div type = "button" class="mr-4 ${color} click-item" onclick="downloadObject(event)">
                  <i class="fas fa-download"></i> Download
                </div>
                <div type = "button" class="mr-4 ${color} click-item" onclick="deleteObject(event)">
                  <i class="fas fa-trash"></i> Delete
                </div>
              </div>
  
            </div>
  
            <div class = "col-md-6 p-3 rounded-right">
              <ul class="nav flex-column">
                <li class="nav-item mw-100">
                  <div class="row p-2">
                    <div class = "col">
                    Timestamp:
                    </div>
                    <div class = "col text-right text-truncate" data-toggle="tooltip" data-placement="right" title="${object.timestamp}">
                    ${object.timestamp}
                    </div>
                  </div>
                </li>
                <li class="nav-item mw-100">
                  <div class="row p-2">
                    <div class = "col text-truncate">
                    Device Version: 
                    </div>
                    <div class = "col text-right text-truncate" data-toggle="tooltip" data-placement="right" title="${current_device.device_version}">
                    ${current_device.device_version}
                    </div>
                  </div>
                </li>
                <li class="nav-item mw-100">
                  <div class="row p-2">
                    <div class = "col text-truncate">
                    EasyUCS Version: 
                    </div>
                    <div class = "col text-right text-truncate" data-toggle="tooltip" data-placement="right" title="${object.easyucs_version}">
                      ${object.easyucs_version}
                    </div>
                  </div>
                </li>
              </ul>
            </div>
          </div>
        </div>
      </div>
  </div>
    `;  
  } else {

    // Editing menu element in base.html to reflect that this is a catalog object
    document.getElementById("navLinkDevices").classList.remove("active");
    document.getElementById("navLinkConfigCatalog").className += " active";
    document.getElementById("navItemConfigCatalog").className += " menu-is-opening menu-open";
    document.getElementById(current_device.device_type + "NavItem").className += " active";

    // Updates the breadcrumb device url with the url of the correct config catalog
    document.getElementById('parent_device_breadcrumb_link').textContent = "Config Catalog";
    document.getElementById('parent_device_breadcrumb_link').href = "/config-catalog/" + current_device.device_type;

    var url_button = '';

    if(object.url){
      url_button = `
      <div type = "button" class="mr-4 ${color} click-item" onclick="window.open('${object.url}', '_blank')">
        <i class="fas fa-eye"></i> More information
      </div>
      `
    }

    document.getElementById('objectDisplayContainer').innerHTML =
    `
    <div>
      <div class="card card-widget widget-user-2 mb-0">
        <div class="widget-user-header p-0">
          <div class = "row m-0">
            <div class = "col-md-6 ${color} p-3 d-flex flex-column justify-content-between rounded-left shadow">
              <div>
                <div class="widget-user-image">
                  <img class="img-circle elevation-2" src=${avatar_src} alt="Device Type">
                </div>
                <h3 class="widget-user-username text-truncate" data-toggle="tooltip" data-placement="top" title="${display_name}">
                ${display_name}
                </h3>
                <h5 class="widget-user-desc text-truncate" data-toggle="tooltip" data-placement="top" title="from Config Catalog">
                from Config Catalog
                </h5>
              </div>
              
              <div class = "d-flex flex-row p-2">
                <div type = "button" class="mr-4 ${color} click-item" onclick="downloadObject(event)">
                  <i class="fas fa-download"></i> Download
                </div>
                ${url_button}
              </div>
  
            </div>
  
            <div class = "col-md-6 p-3 rounded-right">
              <ul class="nav flex-column">
                <li class="nav-item mw-100">
                    <div class="row p-2">
                        <div class = "col">
                        Type: 
                        </div>
                        <div class = "col text-right text-truncate" data-toggle="tooltip" data-placement="right" title="${capitalizeWords(object.category)}">
                        ${capitalizeWords(object.category)}
                        </div>
                    </div>
                </li>
                <li class="nav-item mw-100">
                  <div class="row p-2">
                    <div class = "col text-truncate">
                    Category: 
                    </div>
                    <div class = "col text-right text-truncate" data-toggle="tooltip" data-placement="right" title="${object.subcategory}">
                    ${object.subcategory}
                    </div>
                  </div>
                </li>
                <li class="nav-item mw-100">
                  <div class="row p-2">
                    <div class = "col text-truncate">
                    Revision: 
                    </div>
                    <div class = "col text-right text-truncate" data-toggle="tooltip" data-placement="right" title="${object.revision}">
                      ${object.revision}
                    </div>
                  </div>
                </li>
              </ul>
            </div>
          </div>
        </div>
      </div>
  </div>
    `;  
  }


}

/**
 * Displays an object's JSON in the UI: only for configs and inventories
 */
function displayObjectJson(data){
  if(!data){
    console.error('No data to display!')
    return
  }

  // Changes the object display based on the device type
  if(current_device.device_type == "intersight"){
    color = "bg-info";
  } else if (current_device.device_type == "ucsm"){
    color = "bg-primary";
  } else if (current_device.device_type == "cimc"){
    color = "bg-warning";
  } else if (current_device.device_type == "ucsc"){
    color = "bg-dark";
  }

  const container = document.getElementById("json-display")

  const options = {
  }

  var editor = new JSONEditor(container, options);
  editor.set(JSON.parse(data));

  var editor_main_panel = container.firstElementChild;
  editor_main_panel.classList.add('card');

  editor_main_panel.firstElementChild.classList.add(color);
}

/**
 * Downloads on object
 * @param  {Event} event - The event generated by the click
 */
function downloadObject(event){
  event.preventDefault();
  removeEventPropagation(event);
  download(object_type, device_uuid, object_uuid);
}

/**
 * Gets an object from the API
 */
function getObject(){
  // Displays the object in the UI after getting its data
  getFromDb(displayObject, object_type, device_uuid, object_uuid);

  // If the object is a config or an inventory, we display its JSON in the UI
  if(object_type != "report" && object_type != "backup"){
      getJson(displayObjectJson, object_type, device_uuid, object_uuid);
  }
}

/**
 * Gets the object's corresponding device from the API
 */
function getSingleDevice(){
    // Saves the device object after getting its data
    getFromDb(saveCurrentDevice, "device", device_uuid);
}

/**
 * Redirects the user to the device page
 */
function redirectToDevice(){
    window.history.back();
}

/**
 * Refreshes dynamic data on the page
 */
function refreshData(){
    getSingleDevice();
}

/**
 * Saves the object's associated device
 * @param  {JSON} data - The data returned by getting the device from the API
 */
function saveCurrentDevice(data){
    data = JSON.parse(data);
    current_device = data.device;
    getObject();
}
