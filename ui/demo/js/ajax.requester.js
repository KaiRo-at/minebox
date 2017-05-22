function Requester() {

  var CONFIG = {
    cache: false
  };

  function setMethod(method) {
    CONFIG.method = method;
  }
  function setURL(url) {
    CONFIG.url = url;
  }
  function setData(data) {
    CONFIG.data = data;
  }
  function setType(type) {
    CONFIG.dataType = type;
  }

  function run( successCallback, errorCallback, callback_func ) {
    $.ajax(CONFIG)
    .done(function( data ) {
      successCallback( data );
    })
    .fail(function( response ) {
      errorCallback( response )
    })
    .always(function() {
      if (callback_func) { callback_func(); }
    });
  }

  return {
    setMethod: setMethod,
    setURL: setURL,
    setData: setData,
    setType: setType,
    run: run
  }
}